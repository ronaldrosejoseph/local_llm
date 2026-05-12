#!/usr/bin/env python3
"""
MLX Model Worker — child process for model inference.

Reads JSON commands from stdin, writes JSON responses to stdout.
All MLX library diagnostic output goes to stderr.

Protocol (one JSON object per line, each with a "request_id" field):

  {"command":"load", "request_id":"...", "model_name":"...", "offline":true}
  → {"request_id":"...", "type":"loaded", "model_name":"...", "is_vlm":bool, "context_length":int}

  {"command":"generate", "request_id":"...", "messages":[...], "is_vlm":bool, "stream":bool,
   "max_tokens":int, "temperature":float, "top_p":float, "repetition_penalty":float,
   "images":[...]}
  → stream:  {"request_id":"...", "type":"token", "content":"..."} ... {"request_id":"...", "type":"done"}
  → nonstream: {"request_id":"...", "type":"result", "content":"..."} {"request_id":"...", "type":"done"}

  {"command":"unload", "request_id":"..."}
  → {"request_id":"...", "type":"done"}

  {"command":"ping", "request_id":"..."}
  → {"request_id":"...", "type":"pong"}

  {"command":"shutdown", "request_id":"..."}
  → exits after responding
"""

import sys
import os
import gc
import json
import traceback
import warnings
import logging

# Silence HF env vars before any library imports
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# ── Prevent library diagnostic output from contaminating the IPC channel ──
# Save the real stdout fd for IPC responses, then redirect Python's stdout
# to stderr so that mlx warnings / prints go to stderr instead.
_ipc_out = os.fdopen(os.dup(1), "w", buffering=1)  # line-buffered for IPC

# Redirect all diagnostic output to stderr
sys.stdout = sys.stderr
warnings.filterwarnings("always")
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

# --- Global model state ---
_model = None
_tokenizer = None
_processor = None
_vlm_config = None
_is_vlm = False
_model_name = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _respond(data: dict):
    """Write a single JSON object to the IPC channel (real stdout)."""
    _ipc_out.write(json.dumps(data) + "\n")
    _ipc_out.flush()


def _error(request_id: str, message: str):
    _respond({"request_id": request_id, "type": "error", "message": message})


def _done(request_id: str, **kwargs):
    payload = {"request_id": request_id, "type": "done"}
    if kwargs:
        payload.update(kwargs)
    _respond(payload)


# ---------------------------------------------------------------------------
# Model load / unload
# ---------------------------------------------------------------------------

def _detect_context_length() -> int:
    """Introspect the loaded model for its context window size."""
    context_keys = [
        "max_position_embeddings", "max_seq_len", "context_length",
        "max_sequence_length", "n_positions", "seq_length",
    ]
    nested_keys = ["text_config", "language_config", "llm_config"]

    def _search(config_obj, prefix=""):
        if config_obj is None:
            return None
        for key in context_keys:
            val = getattr(config_obj, key, None)
            if val is None and isinstance(config_obj, dict):
                val = config_obj.get(key)
            if val and isinstance(val, int) and val > 0:
                return val
        for nk in nested_keys:
            sub = getattr(config_obj, nk, None)
            if sub is None and isinstance(config_obj, dict):
                sub = config_obj.get(nk)
            if sub is not None:
                result = _search(sub, prefix=f"{prefix}{nk}.")
                if result:
                    return result
        return None

    if _model is not None:
        for attr in ["config", "args"]:
            config_obj = getattr(_model, attr, None)
            if config_obj is not None:
                result = _search(config_obj, prefix=f"model.{attr}.")
                if result:
                    print(f"[worker] context length {result} from model.{attr}", file=sys.stderr)
                    return result

    if _tokenizer is not None:
        tok_max = getattr(_tokenizer, "model_max_length", None)
        if tok_max and isinstance(tok_max, int) and 1024 < tok_max < 10_000_000:
            print(f"[worker] context length {tok_max} from tokenizer", file=sys.stderr)
            return tok_max

    print("[worker] using default context length 8192", file=sys.stderr)
    return 8192


def _load_model(model_name: str, offline: bool = True):
    """Load a model (VLM-first, LLM-fallback). Sets module-level globals.

    Returns (success, model_name, is_vlm, context_length, error_message).
    """
    global _model, _tokenizer, _processor, _vlm_config, _is_vlm, _model_name

    _unload_model()
    last_error = None

    # Set offline mode
    os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "1" if offline else "0"
    try:
        import huggingface_hub.constants
        huggingface_hub.constants.HF_HUB_OFFLINE = offline
    except Exception:
        pass

    print(f"[worker] loading model: {model_name} (offline={offline})", file=sys.stderr)

    # Attempt 1: VLM
    try:
        import mlx_vlm
        from mlx_vlm.utils import load_config as load_vlm_config

        _model, _processor = mlx_vlm.load(model_name)
        _tokenizer = _processor.tokenizer
        _vlm_config = load_vlm_config(model_name)
        _is_vlm = True
        _model_name = model_name
        ctx_len = _detect_context_length()
        print(f"[worker] loaded as VLM, context_length={ctx_len}", file=sys.stderr)
        return True, model_name, True, ctx_len, None
    except Exception as e:
        last_error = str(e)
        print(f"[worker] VLM load failed: {e}", file=sys.stderr)

    # Attempt 2: standard LLM
    try:
        import mlx_lm
        from mlx_lm import load

        _model, _tokenizer = load(model_name)
        _processor = None
        _vlm_config = None
        _is_vlm = False
        _model_name = model_name
        ctx_len = _detect_context_length()
        print(f"[worker] loaded as LLM, context_length={ctx_len}", file=sys.stderr)
        return True, model_name, False, ctx_len, None
    except Exception as e:
        last_error = str(e)
        print(f"[worker] LLM load failed: {e}", file=sys.stderr)

    # Attempt 3: retry with networking enabled (if first attempt was offline)
    if offline:
        print("[worker] retrying with networking enabled", file=sys.stderr)
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "0"
        try:
            import huggingface_hub.constants
            huggingface_hub.constants.HF_HUB_OFFLINE = False
        except Exception:
            pass

        try:
            import mlx_vlm
            from mlx_vlm.utils import load_config as load_vlm_config
            _model, _processor = mlx_vlm.load(model_name)
            _tokenizer = _processor.tokenizer
            _vlm_config = load_vlm_config(model_name)
            _is_vlm = True
            _model_name = model_name
            ctx_len = _detect_context_length()
            return True, model_name, True, ctx_len, None
        except Exception:
            pass

        try:
            import mlx_lm
            from mlx_lm import load
            _model, _tokenizer = load(model_name)
            _processor = None
            _vlm_config = None
            _is_vlm = False
            _model_name = model_name
            ctx_len = _detect_context_length()
            return True, model_name, False, ctx_len, None
        except Exception as e2:
            last_error = str(e2)
            print(f"[worker] retry with networking also failed: {e2}", file=sys.stderr)
            return False, model_name, False, 8192, last_error

    _model_name = None
    return False, model_name, False, 8192, last_error


def _unload_model():
    """Free VRAM held by the current model."""
    global _model, _tokenizer, _processor, _vlm_config, _is_vlm, _model_name

    _model = None
    _tokenizer = None
    _processor = None
    _vlm_config = None
    _is_vlm = False
    _model_name = None

    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except Exception:
        pass
    print("[worker] model unloaded", file=sys.stderr)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _generate_inner(request_id: str, messages: list, is_vlm: bool, stream: bool,
                    max_tokens: int, temperature: float, top_p: float,
                    repetition_penalty: float, images: list):
    """Core generation — applies template, calls mlx, writes token/result responses."""
    if _model is None or _tokenizer is None:
        _error(request_id, "No model loaded")
        _done(request_id)
        return

    try:
        if is_vlm and _processor is not None:
            import mlx_vlm
            from mlx_vlm.prompt_utils import apply_chat_template as apply_vlm_template

            num_images = len(images) if images else 0
            prompt = apply_vlm_template(_processor, _vlm_config, messages, num_images=num_images)

            if stream:
                last = None
                for response in mlx_vlm.stream_generate(
                    _model, _processor,
                    prompt=prompt,
                    image=images if images else None,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    _respond({
                        "request_id": request_id,
                        "type": "token",
                        "content": response.text,
                    })
                    last = response
                # Forward MLX-computed stats via _done
                gen_tokens = getattr(last, "generation_tokens", 0) if last else 0
                gen_tps = getattr(last, "generation_tps", 0.0) if last else 0.0
                _done(request_id,
                      generation_tokens=gen_tokens,
                      tokens_per_second=round(gen_tps, 2) if gen_tps else 0)
            else:
                result = mlx_vlm.generate(
                    _model, _processor,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    verbose=False,
                )
                text = result if isinstance(result, str) else result.text
                _respond({"request_id": request_id, "type": "result", "content": text.strip()})
                _done(request_id)
        else:
            import mlx_lm
            from mlx_lm.sample_utils import make_sampler, make_repetition_penalty

            prompt = _tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            sampler = make_sampler(temp=temperature, top_p=top_p)
            logits_processors = []
            if repetition_penalty and repetition_penalty > 1.0:
                logits_processors.append(make_repetition_penalty(penalty=repetition_penalty))

            if stream:
                last = None
                for response in mlx_lm.stream_generate(
                    _model, _tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    sampler=sampler,
                    logits_processors=logits_processors,
                ):
                    _respond({
                        "request_id": request_id,
                        "type": "token",
                        "content": response.text,
                    })
                    last = response
                # Forward MLX-computed stats via _done
                gen_tokens = getattr(last, "generation_tokens", 0) if last else 0
                gen_tps = getattr(last, "generation_tps", 0.0) if last else 0.0
                _done(request_id,
                      generation_tokens=gen_tokens,
                      tokens_per_second=round(gen_tps, 2) if gen_tps else 0)
            else:
                result = mlx_lm.generate(
                    _model, _tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    verbose=False,
                )
                text = result if isinstance(result, str) else result.text
                _respond({"request_id": request_id, "type": "result", "content": text.strip()})
                _done(request_id)
    except Exception as e:
        _error(request_id, str(e))
        _done(request_id)


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

def _handle_command(cmd: dict):
    """Route a command to the appropriate handler."""
    command = cmd.get("command")
    request_id = cmd.get("request_id", "unknown")

    if command == "load":
        model_name = cmd.get("model_name", "")
        offline = cmd.get("offline", True)
        if not model_name:
            _error(request_id, "model_name is required")
            return
        success, name, is_vlm, ctx_len, err = _load_model(model_name, offline=offline)
        if success:
            _respond({
                "request_id": request_id,
                "type": "loaded",
                "model_name": name,
                "is_vlm": is_vlm,
                "context_length": ctx_len,
            })
        else:
            _error(request_id, err or f"Failed to load model: {name}")

    elif command == "generate":
        _generate_inner(
            request_id=request_id,
            messages=cmd.get("messages", []),
            is_vlm=cmd.get("is_vlm", False),
            stream=cmd.get("stream", True),
            max_tokens=cmd.get("max_tokens", 8192),
            temperature=cmd.get("temperature", 0.3),
            top_p=cmd.get("top_p", 0.9),
            repetition_penalty=cmd.get("repetition_penalty", 1.1),
            images=cmd.get("images"),
        )

    elif command == "unload":
        _unload_model()
        _done(request_id)

    elif command == "ping":
        _respond({"request_id": request_id, "type": "pong", "model_name": _model_name})

    elif command == "shutdown":
        _unload_model()
        _respond({"request_id": request_id, "type": "done"})
        sys.exit(0)

    else:
        _error(request_id, f"Unknown command: {command}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    print("[worker] started, reading commands from stdin", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            _handle_command(cmd)
        except json.JSONDecodeError as e:
            print(f"[worker] invalid JSON: {e}", file=sys.stderr)
            _respond({"request_id": "unknown", "type": "error", "message": f"Invalid JSON: {e}"})
        except Exception as e:
            print(f"[worker] unexpected error: {traceback.format_exc()}", file=sys.stderr)
            _respond({"request_id": "unknown", "type": "error", "message": str(e)})
    print("[worker] stdin closed, exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
