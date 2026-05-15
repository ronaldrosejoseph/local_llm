"""
Model Process Manager — manages the MLX worker child process.

Provides async and sync APIs for model loading, streaming/non-streaming
generation, health checks, and crash recovery.
"""

import os
import re
import sys
import gc
import json
import uuid
import asyncio
import subprocess
import signal
import threading
from pathlib import Path
from contextlib import closing

from server import state
from server.db import get_db_connection


# Path to the worker script (same directory as this file)
_WORKER_PATH = Path(__file__).resolve().parent / "worker.py"


class InferenceCrash(Exception):
    """Raised when the child worker process dies during generation."""
    pass


class ModelManager:
    """Manages the MLX worker child process lifecycle.

    Only ONE ModelManager instance should exist per server process.
    All model operations (load, generate, unload) are proxied to the child.
    """

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.model_name: str | None = None
        self.is_vlm: bool = False
        self.context_length: int = 8192

        self._pending: dict[str, asyncio.Queue] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._ping_fail_count = 0
        self._shutting_down = False
        self._last_load_error = None  # surfaced to frontend on failed model loads
        self._stderr_tail: list[str] = []  # last N stderr lines for crash diagnostics

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Spawn the worker child process and load the active model."""
        self._loop = asyncio.get_running_loop()
        self._shutting_down = False

        # Kill any orphan workers from a previous crash
        _kill_orphan_workers()

        self._spawn_process()

        # Start the background reader that dispatches child stdout lines
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Start periodic health checks
        self._health_task = asyncio.create_task(self._health_loop())

        # Load the active model from DB
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT name FROM models WHERE active = 1").fetchone()
        model_name = row["name"] if row else state.DEFAULT_MODEL

        success, name = await self.load_model(model_name)

        # Note: load_model internally handles falling back to state.DEFAULT_MODEL
        # if the requested model fails, so 'name' is guaranteed to be the best available model.
        state.MODEL_NAME = name
        print(f"ModelManager: started with model={name}, is_vlm={self.is_vlm}", file=sys.stderr)

    async def stop(self):
        """Gracefully shut down the worker process."""
        self._shutting_down = True

        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.process and self.process.poll() is None:
            try:
                await self._send_raw({"command": "shutdown", "request_id": str(uuid.uuid4())})
                await asyncio.sleep(0.5)
            except Exception:
                pass

            # SIGTERM then SIGKILL
            try:
                self.process.terminate()
                await asyncio.sleep(1)
                if self.process.poll() is None:
                    self.process.kill()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # Drain pending queues
        for q in list(self._pending.values()):
            try:
                q.put_nowait({"type": "error", "message": "Server shutting down"})
            except asyncio.QueueFull:
                pass
        self._pending.clear()

        # Close pipes to prevent ResourceWarning about unclosed files
        if self.process:
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
        self.process = None

    # ------------------------------------------------------------------
    # Model operations
    # ------------------------------------------------------------------

    async def load_model(self, model_name: str, offline: bool = True) -> tuple[bool, str]:
        """Load a model in the child process. Returns (success, actual_model_name).

        If the requested model fails to load, automatically falls back to the
        default model and returns its name on failure.
        """
        cmd = {
            "command": "load",
            "request_id": str(uuid.uuid4()),
            "model_name": model_name,
            "offline": offline,
        }

        await self._send_raw(cmd)

        load_success = False
        actual_name = model_name

        async for resp in self._read_responses(cmd["request_id"]):
            if resp.get("type") == "loaded":
                self.model_name = resp.get("model_name", model_name)
                self.is_vlm = resp.get("is_vlm", False)
                self.context_length = resp.get("context_length", 8192)
                _update_model_type_in_db(model_name, self.is_vlm)
                load_success = True
                actual_name = self.model_name
                break
            elif resp.get("type") == "error":
                self._last_load_error = resp.get("message", "Unknown error")
                break

        if load_success:
            return True, actual_name

        # Model failed — fall back to default if we aren't already trying to load it
        fallback = state.DEFAULT_MODEL
        if model_name != fallback:
            print(f"ModelManager: failed to load {model_name}, falling back to {fallback}", file=sys.stderr)
            # Recurse once to the default model. The 'if' check above prevents infinite recursion.
            _, name = await self.load_model(fallback, offline)
            return False, name

        return False, fallback

    async def unload_model(self):
        """Tell the child to unload its model (frees VRAM for FLUX)."""
        if self.process is None or self.process.poll() is not None:
            return
        cmd = {"command": "unload", "request_id": str(uuid.uuid4())}
        await self._send_raw(cmd)
        async for _ in self._read_responses(cmd["request_id"]):
            pass
        self.model_name = None
        self.is_vlm = False

    async def stream_generate(self, messages: list, is_vlm: bool = False,
                              image_paths: list = None, max_tokens: int = 8192,
                              temperature: float = 0.3, top_p: float = 0.9,
                              repetition_penalty: float = 1.1,
                              thinking_end_tag: str = None):
        """Stream tokens from the child process. Yields (type, text) tuples.

        When thinking_end_tag is set and the worker detects the tag in the
        output, it yields ("thinking_start", ""), ("thinking", text),
        ("thinking_done", "") before the regular ("token", text) events.

        After the generator is exhausted, read self._last_gen_stats for
        MLX-computed tokens_per_second and generation_tokens (if available).
        """
        self._last_gen_stats = None
        cmd = {
            "command": "generate",
            "request_id": str(uuid.uuid4()),
            "messages": messages,
            "is_vlm": is_vlm,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "repetition_penalty": repetition_penalty,
        }
        if image_paths:
            cmd["images"] = image_paths
        if thinking_end_tag:
            cmd["thinking_end_tag"] = thinking_end_tag

        await self._send_raw(cmd)
        async for resp in self._read_responses(cmd["request_id"]):
            t = resp.get("type")
            if t in ("token", "thinking", "thinking_start", "thinking_done"):
                yield (t, resp.get("content", ""))
            elif t == "done":
                # Capture MLX-reported stats from the done response
                if resp.get("tokens_per_second"):
                    self._last_gen_stats = {
                        "tokens_per_second": resp.get("tokens_per_second", 0),
                        "generation_tokens": resp.get("generation_tokens", 0),
                    }
                return
            # error types are now raised as InferenceCrash inside _read_responses

    async def nonstream_generate(self, messages: list, is_vlm: bool = False,
                                 max_tokens: int = 300, temperature: float = 0.3,
                                 top_p: float = 0.9, repetition_penalty: float = 1.1) -> str:
        """Non-streaming generation. Returns the complete response text."""
        cmd = {
            "command": "generate",
            "request_id": str(uuid.uuid4()),
            "messages": messages,
            "is_vlm": is_vlm,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "repetition_penalty": repetition_penalty,
        }

        await self._send_raw(cmd)
        result = ""
        async for resp in self._read_responses(cmd["request_id"]):
            t = resp.get("type")
            if t == "result":
                result = resp.get("content", "")
            elif t == "error":
                return ""
            elif t == "done":
                return result
        return result

    # ------------------------------------------------------------------
    # Synchronous wrappers (for thread-based callers like FLUX)
    # ------------------------------------------------------------------

    def sync_unload_model(self, timeout: float = 120):
        """Thread-safe synchronous unload."""
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.unload_model(), self._loop)
        return future.result(timeout=timeout)

    def sync_load_model(self, model_name: str, timeout: float = 300):
        """Thread-safe synchronous load."""
        if self._loop is None:
            return False, model_name
        future = asyncio.run_coroutine_threadsafe(self.load_model(model_name), self._loop)
        return future.result(timeout=timeout)

    def sync_nonstream_generate(self, messages: list, is_vlm: bool = False,
                                max_tokens: int = 300, timeout: float = 60) -> str:
        """Thread-safe synchronous non-streaming generation."""
        if self._loop is None:
            return ""
        future = asyncio.run_coroutine_threadsafe(
            self.nonstream_generate(messages, is_vlm=is_vlm, max_tokens=max_tokens),
            self._loop,
        )
        return future.result(timeout=timeout)

    # ------------------------------------------------------------------
    # Thinking model detection
    # ------------------------------------------------------------------

    # Known end-tag patterns used by thinking/reasoning models.
    # These appear after the internal chain-of-thought and before the final reply.
    _THINKING_END_PATTERNS = re.compile(
        r'(</think>|'           # DeepSeek-R1, Qwen (QwQ), most common
        r'<channel\|>|'         # IBM Granite 3.x
        r'◁/think▷|'            # alternative encoded think tag
        r'<\|end\|>|'           # some custom distilled models
        r'<unused95>|'          # legacy distilled model marker
        r'</thinking>|'         # Llama-based reasoning
        r'</reasoning>|'        # generic reasoning wrapper
        r'</thought>|'          # older / alternate naming
        r'</answer>|'           # some chat templates
        r'</response>)'         # Claude-style thinking wrapper
    )

    def sync_detect_thinking(self, model_name: str, timeout: float = 120) -> tuple[bool, str | None]:
        """Detect model capabilities on first load.

        Sends a short prompt then scans the response for thinking end-tag
        patterns. Also persists VLM/LM type determined by the worker during
        loading (so supports_vision is never left NULL).

        Returns (has_thinking, thinking_end_tag).
        Side effect: persists thinking + vision info to the models table.
        """
        # Persist VLM type (worker determined this during load)
        _update_model_type_in_db(model_name, self.is_vlm)

        # Use a generous token budget — thinking models burn tokens on
        # chain-of-thought before emitting the end tag.
        hi_response = self.sync_nonstream_generate(
            [{"role": "user", "content": "Hi"}],
            is_vlm=self.is_vlm,
            max_tokens=2048,
            timeout=120,
        )
        if not hi_response:
            self._persist_thinking_result(model_name, has_thinking=False, end_tag=None)
            return False, None

        match = self._THINKING_END_PATTERNS.search(hi_response)
        if match:
            end_tag = match.group(0)
            self._persist_thinking_result(model_name, has_thinking=True, end_tag=end_tag)
            return True, end_tag

        self._persist_thinking_result(model_name, has_thinking=False, end_tag=None)
        return False, None

    def _persist_thinking_result(self, model_name: str, has_thinking: bool, end_tag: str | None):
        """Store the thinking detection result in the models table.

        Only updates if has_thinking is still NULL — avoids overwriting
        a racing detection from another request (unlikely but safe).
        """
        try:
            with closing(get_db_connection()) as conn:
                conn.execute(
                    "UPDATE models SET has_thinking = ?, thinking_end_tag = ? "
                    "WHERE name = ? AND has_thinking IS NULL",
                    (1 if has_thinking else 0, end_tag, model_name),
                )
                conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal: subprocess management
    # ------------------------------------------------------------------

    def _spawn_process(self):
        """Spawn the worker.py child process."""
        worker_path = str(_WORKER_PATH)
        python_exe = sys.executable

        # Pass HF_TOKEN to the worker process (for gated model access)
        env = os.environ.copy()
        if os.environ.get("HF_TOKEN"):
            env["HF_TOKEN"] = os.environ["HF_TOKEN"]

        self.process = subprocess.Popen(
            [python_exe, worker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            env=env,
        )
        print(f"ModelManager: spawned worker PID={self.process.pid}", file=sys.stderr)

        # Start a stderr reader to log worker diagnostics
        asyncio.create_task(self._log_stderr())

    async def _log_stderr(self):
        """Read worker stderr, log it, and keep a tail buffer for crash diagnostics."""
        if self.process is None or self.process.stderr is None:
            return
        loop = asyncio.get_running_loop()
        MAX_TAIL = 20

        def _read():
            try:
                return self.process.stderr.readline()
            except Exception:
                return ""

        while self.process and self.process.poll() is None:
            try:
                line = await loop.run_in_executor(None, _read)
                if line:
                    stripped = line.rstrip()
                    print(f"[worker:{self.process.pid}] {stripped}", file=sys.stderr)
                    self._stderr_tail.append(stripped)
                    if len(self._stderr_tail) > MAX_TAIL:
                        self._stderr_tail = self._stderr_tail[-MAX_TAIL:]
                else:
                    break
            except Exception:
                break

    async def _send_raw(self, data: dict):
        """Send a JSON command to the child's stdin (thread-safe)."""
        async with self._send_lock:
            if self.process is None or self.process.poll() is not None:
                # Worker is dead — trigger recovery before sending
                raise InferenceCrash("Worker process is not running")

            line = json.dumps(data) + "\n"
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise InferenceCrash(f"Failed to send to worker: {e}") from e

    async def _read_responses(self, request_id: str):
        """Async generator that yields responses for a given request_id.

        The background reader task puts responses into self._pending[request_id].
        Also proactively checks worker process health on every iteration so
        an OOM kill is detected even if the reader task hasn't run yet.
        Has a hard 10-minute total timeout to prevent infinite blocking.
        """
        import time
        q: asyncio.Queue = asyncio.Queue()
        self._pending[request_id] = q
        started = time.monotonic()
        _MAX_WAIT = 600  # 10 minutes max total wait

        try:
            while True:
                # Hard timeout — don't block forever
                if time.monotonic() - started > _MAX_WAIT:
                    raise InferenceCrash("Generation timed out — worker unresponsive")

                # Check worker health before each blocking wait
                if self.process is None or self.process.poll() is not None:
                    raise InferenceCrash("Model process terminated unexpectedly")

                # Wait for a response (with periodic health re-check)
                try:
                    resp = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue  # re-check health at top of loop

                yield resp
                t = resp.get("type", "")
                if t == "error":
                    raise InferenceCrash(resp.get("message", "Worker process error"))
                if t == "done":
                    return
        except asyncio.CancelledError:
            raise
        finally:
            self._pending.pop(request_id, None)

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    async def _reader_loop(self):
        """Read lines from child stdout and dispatch to pending queues."""
        if self.process is None or self.process.stdout is None:
            return

        loop = asyncio.get_running_loop()

        def _readline():
            try:
                return self.process.stdout.readline()
            except Exception:
                return ""

        while not self._shutting_down:
            try:
                line = await loop.run_in_executor(None, _readline)
            except Exception:
                break

            if not line:
                # EOF — child process died
                await self._handle_child_exit()
                break

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print(f"ModelManager: invalid JSON from worker: {line[:200]}", file=sys.stderr)
                continue

            request_id = data.get("request_id", "")
            q = self._pending.get(request_id)
            if q is not None:
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    pass

    async def _handle_child_exit(self):
        """Called when the child's stdout closes (process died)."""
        if self._shutting_down:
            return

        exit_code = None
        if self.process:
            exit_code = self.process.poll()
        print(f"ModelManager: worker exited with code={exit_code}", file=sys.stderr)

        # Push error to all pending request queues — include relevant crash lines
        crash_detail = _extract_crash_detail(self._stderr_tail)
        _notify_pending_crash(self._pending, crash_detail)

        # Trigger crash recovery
        await self._crash_recovery()

    async def _crash_recovery(self):
        """Restart the worker with the fallback model after a crash."""
        print("ModelManager: starting crash recovery...", file=sys.stderr)

        # Kill old process
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
                await asyncio.sleep(0.5)
            except Exception:
                pass
        self.process = None

        # Cancel reader task if it's still running (but not if we ARE the reader)
        if self._reader_task is not None:
            if self._reader_task is not asyncio.current_task() and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass
            self._reader_task = None

        # Kill any leftover orphans
        _kill_orphan_workers()

        # Spawn fresh process
        self._spawn_process()
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Load fallback model
        fallback = state.DEFAULT_MODEL
        print(f"ModelManager: loading fallback model {fallback}", file=sys.stderr)
        success, name = await self.load_model(fallback)

        if not success:
            print(f"ModelManager: CRITICAL — even fallback model failed to load", file=sys.stderr)
            return

        # Update global state
        state.MODEL_NAME = name

        # Update DB
        with closing(get_db_connection()) as conn:
            conn.execute("UPDATE models SET active = 0")
            conn.execute("UPDATE models SET active = 1 WHERE name = ?", (fallback,))
            conn.commit()

        self._ping_fail_count = 0
        print(f"ModelManager: crash recovery complete, now running {name}", file=sys.stderr)

    async def cancel_generation(self):
        """Kill the worker and restart with the current model.

        Called when the user stops generation. The worker process is stuck
        in the MLX generation loop and won't read new commands until it
        finishes — so we kill it and reload the same model fresh.
        """
        current_model = state.MODEL_NAME or self.model_name
        print(f"ModelManager: cancelling generation, will reload {current_model}", file=sys.stderr)

        # Kill old process
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
                await asyncio.sleep(0.5)
            except Exception:
                pass
        self.process = None

        # Cancel reader task
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        # Drain pending queues
        for q in list(self._pending.values()):
            try:
                q.put_nowait({"type": "error", "message": "Generation cancelled by user"})
            except asyncio.QueueFull:
                pass
            try:
                q.put_nowait({"type": "done"})
            except asyncio.QueueFull:
                pass
        self._pending.clear()

        # Kill orphans
        _kill_orphan_workers()

        # Spawn fresh process
        self._spawn_process()
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Reload the same model
        if current_model:
            success, name = await self.load_model(current_model)
            state.MODEL_NAME = name
            if not success:
                print(f"ModelManager: cancel restart — failed to reload {current_model}, falling back", file=sys.stderr)
            else:
                print(f"ModelManager: cancel restart complete, reloaded {name}", file=sys.stderr)

        self._ping_fail_count = 0

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def _health_loop(self):
        """Periodically ping the child process.

        Pings are skipped during active generation (the serial stdin/stdout
        channel can't handle concurrent commands). The generation itself has
        its own timeout via _read_responses. Recovery triggers only after 3
        consecutive ping failures when idle (no pending generation).
        """
        await asyncio.sleep(15)  # initial delay

        while not self._shutting_down:
            try:
                alive = await self._ping()
                if alive:
                    self._ping_fail_count = 0
                else:
                    self._ping_fail_count += 1
                    if self._ping_fail_count >= 3:
                        print("ModelManager: health check failed 3 times, triggering recovery", file=sys.stderr)
                        crash_detail = _extract_crash_detail(self._stderr_tail)
                        _notify_pending_crash(self._pending, crash_detail)
                        await self._crash_recovery()
            except Exception:
                self._ping_fail_count += 1
                if self._ping_fail_count >= 3:
                    crash_detail = _extract_crash_detail(self._stderr_tail)
                    _notify_pending_crash(self._pending, crash_detail)
                    await self._crash_recovery()

            await asyncio.sleep(15)

    async def _ping(self) -> bool:
        """Send ping and wait for pong. Returns True if child is healthy.

        Skips the ping when generation is active (pending requests present)
        because the serial stdin/stdout protocol means the worker cannot
        respond to new commands while streaming tokens. The generation
        itself has its own timeout and health checks in _read_responses.
        """
        if self.process is None or self.process.poll() is not None:
            return False

        # Skip ping during active generation — the worker is busy streaming
        # and cannot process a concurrent command on the serial channel.
        if self._pending:
            return True

        cmd = {"command": "ping", "request_id": str(uuid.uuid4())}
        try:
            await self._send_raw(cmd)
        except InferenceCrash:
            return False

        try:
            async with asyncio.timeout(5):
                async for resp in self._read_responses(cmd["request_id"]):
                    if resp.get("type") == "pong":
                        return True
        except (asyncio.TimeoutError, Exception):
            return False
        return False


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _extract_crash_detail(stderr_tail: list[str]) -> str:
    """Extract only the crash-relevant lines from the worker's stderr tail."""
    crash_keywords = (
        "error", "exception", "terminating", "fatal", "insufficient",
        "memory", "killed", "abort", "traceback", "signal", "segfault",
        "bus error", "metal", "out of memory", "oom",
    )
    relevant = []
    for line in stderr_tail[-15:]:
        line_lower = line.lower()
        if any(kw in line_lower for kw in crash_keywords):
            relevant.append(line)
    return "\n".join(relevant[-3:])  # at most the last 3 crash lines


def _notify_pending_crash(pending: dict, detail: str = ""):
    """Push error+done to every pending request queue so generators wake up."""
    msg = "Model process crashed (OOM or unexpected error)"
    if detail:
        msg += "\n\n" + detail
    for rid, q in list(pending.items()):
        try:
            q.put_nowait({"request_id": rid, "type": "error", "message": msg})
        except asyncio.QueueFull:
            pass
        try:
            q.put_nowait({"request_id": rid, "type": "done"})
        except asyncio.QueueFull:
            pass


# ------------------------------------------------------------------
# Orphan cleanup
# ------------------------------------------------------------------

def _update_model_type_in_db(model_name: str, is_vlm: bool):
    """Persist model type from worker load result to DB.

    Only fills in NULL (not-yet-verified) entries. Once a type is set
    it is not overwritten — this preserves manual corrections for models
    where mlx_vlm.load() fails but vision still works (e.g. quantized
    models with stripped vision tower signatures).
    """
    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE models SET supports_vision = ?, is_downloaded = 1 "
                "WHERE name = ? AND supports_vision IS NULL",
                (1 if is_vlm else 0, model_name),
            )
            conn.commit()
    except Exception:
        pass


def _kill_orphan_workers():
    """Kill any leftover worker.py processes from previous runs."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "worker.py"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split()
            current_pid = str(os.getpid())
            for pid in pids:
                if pid != current_pid:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                        print(f"ModelManager: killed orphan worker PID={pid}", file=sys.stderr)
                    except OSError:
                        pass
    except Exception:
        pass
