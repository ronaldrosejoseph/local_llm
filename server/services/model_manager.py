"""
Model Process Manager — manages the MLX worker child process.

Provides async and sync APIs for model loading, streaming/non-streaming
generation, health checks, and crash recovery.
"""

import os
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
        if not success:
            # Fallback to default
            print(f"ModelManager: failed to load {model_name}, falling back to {state.DEFAULT_MODEL}", file=sys.stderr)
            success, name = await self.load_model(state.DEFAULT_MODEL)

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
        self.process = None

    # ------------------------------------------------------------------
    # Model operations
    # ------------------------------------------------------------------

    async def load_model(self, model_name: str, offline: bool = True) -> tuple[bool, str]:
        """Load a model in the child process. Returns (success, actual_model_name)."""
        cmd = {
            "command": "load",
            "request_id": str(uuid.uuid4()),
            "model_name": model_name,
            "offline": offline,
        }
        responses = []
        await self._send_raw(cmd)
        async for resp in self._read_responses(cmd["request_id"]):
            responses.append(resp)
            if resp.get("type") == "loaded":
                self.model_name = resp.get("model_name", model_name)
                self.is_vlm = resp.get("is_vlm", False)
                self.context_length = resp.get("context_length", 8192)
                return True, self.model_name
            elif resp.get("type") == "error":
                return False, model_name
        return False, model_name

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
                              repetition_penalty: float = 1.1):
        """Stream tokens from the child process. Yields token strings."""
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

        await self._send_raw(cmd)
        async for resp in self._read_responses(cmd["request_id"]):
            t = resp.get("type")
            if t == "token":
                yield resp.get("content", "")
            elif t == "error":
                yield resp.get("message", "Unknown error")
            elif t == "done":
                return

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
    # Internal: subprocess management
    # ------------------------------------------------------------------

    def _spawn_process(self):
        """Spawn the worker.py child process."""
        worker_path = str(_WORKER_PATH)
        python_exe = sys.executable

        self.process = subprocess.Popen(
            [python_exe, worker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # captured so stderr doesn't leak into IPC
            text=True,
            bufsize=1,  # line-buffered
        )
        print(f"ModelManager: spawned worker PID={self.process.pid}", file=sys.stderr)

        # Start a stderr reader to log worker diagnostics
        asyncio.create_task(self._log_stderr())

    async def _log_stderr(self):
        """Read worker stderr and log it."""
        if self.process is None or self.process.stderr is None:
            return
        loop = asyncio.get_running_loop()

        def _read():
            try:
                return self.process.stderr.readline()
            except Exception:
                return ""

        while self.process and self.process.poll() is None:
            try:
                line = await loop.run_in_executor(None, _read)
                if line:
                    print(f"[worker:{self.process.pid}] {line.rstrip()}", file=sys.stderr)
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
        When it sees 'done' or 'error', it signals completion.
        """
        q: asyncio.Queue = asyncio.Queue()
        self._pending[request_id] = q

        try:
            while True:
                resp = await q.get()
                yield resp
                t = resp.get("type", "")
                if t in ("done", "error"):
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

        # Push error to all pending request queues
        for rid, q in list(self._pending.items()):
            try:
                q.put_nowait({"request_id": rid, "type": "error",
                               "message": "Model process crashed (OOM or unexpected error)"})
            except asyncio.QueueFull:
                pass
            try:
                q.put_nowait({"request_id": rid, "type": "done"})
            except asyncio.QueueFull:
                pass

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

        # Clear pending queues
        self._pending.clear()

        # Cancel reader (will restart)
        if self._reader_task:
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

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def _health_loop(self):
        """Periodically ping the child process."""
        await asyncio.sleep(10)  # initial delay

        while not self._shutting_down:
            try:
                alive = await self._ping()
                if alive:
                    self._ping_fail_count = 0
                else:
                    self._ping_fail_count += 1
                    if self._ping_fail_count >= 2:
                        print("ModelManager: health check failed twice, triggering recovery", file=sys.stderr)
                        await self._crash_recovery()
            except Exception:
                self._ping_fail_count += 1
                if self._ping_fail_count >= 2:
                    await self._crash_recovery()

            await asyncio.sleep(10)

    async def _ping(self) -> bool:
        """Send ping and wait for pong. Returns True if child is healthy."""
        if self.process is None or self.process.poll() is not None:
            return False

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
# Orphan cleanup
# ------------------------------------------------------------------

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
