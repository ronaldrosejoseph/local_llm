"""
Image generation service — shared FLUX pipeline for /imagine and /edit.

Eliminates the ~90% code duplication between the two handlers by
parameterizing the differences (steps, source image, status messages).
"""

import os
import gc
import json
import queue
import asyncio
import threading
import uuid

from contextlib import closing

from server import state
from server.config import load_config
from server.db import get_db_connection


def _ensure_flux_downloaded(q: queue.Queue):
    """Pre-download FLUX.1-schnell model repos if not already cached.

    Calls huggingface_hub.snapshot_download directly (bypassing mflux's own
    resolution) so we can surface clear errors before the heavy Flux1()
    constructor runs.  Only downloads repos whose snapshots are missing.
    """
    from huggingface_hub import snapshot_download

    # The repos and file patterns that FLUX.1-schnell needs
    flux_repo = "black-forest-labs/FLUX.1-schnell"
    flux_patterns = [
        "text_encoder/*.safetensors", "text_encoder/*.json",
        "text_encoder_2/*.safetensors", "text_encoder_2/*.json",
        "transformer/*.safetensors", "transformer/*.json",
        "vae/*.safetensors", "vae/*.json",
        "tokenizer/**", "tokenizer_2/**",
    ]

    from server.services.llm import is_model_cached
    if is_model_cached(flux_repo):
        return  # Already cached, nothing to do

    # Explicitly pass the token so gated-repo downloads succeed
    token = os.environ.get("HF_TOKEN")
    q.put({"model_badge": "Downloading FLUX...", "model_badge_pulse": True})
    snapshot_download(repo_id=flux_repo, allow_patterns=flux_patterns, token=token)


def run_flux_pipeline(prompt: str, chat_id: str, q: queue.Queue, img_name: str,
                      source_image_path: str = None, strength: float = 0.15,
                      steps: int = 4, result_message: str = "Here is the image you requested:"):
    """
    Shared FLUX generation thread target.

    Unloads the LLM, runs FLUX.1 Schnell, saves the image, reloads the LLM,
    and communicates progress back via the queue.

    HARDENED: try/finally guarantees LLM reload even if FLUX crashes.
    Concurrency: acquires generation_lock for the full lifecycle.
    """
    state.generation_lock.acquire()
    try:
        # Temporarily go online so mflux can download FLUX weights.
        # Must happen BEFORE load_hf_token so the login call succeeds.
        from server.services.llm import set_offline_mode
        set_offline_mode(False)

        # Ensure HF token is active for gated model access (FLUX.1)
        try:
            from server.services.hf_auth import load_hf_token
            load_hf_token()
        except Exception:
            pass

        from mflux.models.common.config import ModelConfig
        from mflux.models.flux.variants.txt2img.flux import Flux1
        from mflux.callbacks.callback import InLoopCallback
        import time

        # Unload LLM from child process to free VRAM for FLUX
        if state.model_manager:
            state.model_manager.sync_unload_model()

        os.makedirs("static/images", exist_ok=True)

        # Pre-download FLUX repos before the heavy Flux1() constructor.
        # This gives us clean error messages if the download fails.
        _ensure_flux_downloaded(q)

        class ProgressCB(InLoopCallback):
            def call_in_loop(self, t, seed, prompt, latents, config, time_steps, **kwargs):
                if time_steps and time_steps.total > 0:
                    progress = int((time_steps.n / time_steps.total) * 100)
                    q.put({"progress": progress})

        # Signal badge: loading the image model
        q.put({"model_badge": "Loading FLUX...", "model_badge_pulse": True})

        flux = Flux1(
            model_config=ModelConfig.from_name(model_name="schnell"),
            quantize=4
        )
        flux.callbacks.register(ProgressCB())

        # Restore offline mode now that FLUX is loaded
        set_offline_mode(True)

        # Signal badge: image model is now active
        q.put({"model_badge": "FLUX.1 schnell", "model_badge_pulse": False})

        cfg_gen = load_config()
        res_parts = cfg_gen.get("image_generation_resolution", "720x720").split("x")
        w, h = int(res_parts[0]), int(res_parts[1])

        gen_kwargs = dict(
            seed=int(time.time()),
            prompt=prompt,
            num_inference_steps=steps,
            width=w,
            height=h,
        )
        if source_image_path:
            gen_kwargs["image_path"] = source_image_path
            gen_kwargs["image_strength"] = strength

        image = flux.generate_image(**gen_kwargs)

        img_path = f"static/images/{img_name}"
        image.save(path=img_path)

        del flux
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

        markdown_img = f"![{prompt}](/images/{img_name})\n"
        assistant_full_reply = f"{result_message}\n\n{markdown_img}"
        with closing(get_db_connection()) as conn:
            conn.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                (chat_id, "assistant", assistant_full_reply)
            )
            conn.commit()

        q.put({"image": img_name})
    except Exception as e:
        import traceback
        print(f"FLUX error: {e}")
        traceback.print_exc()
        err_str = str(e)
        err_lower = err_str.lower()
        # Detect gated repository / terms-not-accepted errors (specific HF auth issues)
        if any(kw in err_lower for kw in ("gated", "terms", "repository not found")) or (
            any(code in err_lower for code in ("401", "403"))
            and any(kw in err_lower for kw in ("unauthorized", "forbidden", "token", "gated"))
        ):
            err_str = (
                "HuggingFace requires you to accept the terms for **FLUX.1-schnell**.\n\n"
                "1. Log into your HuggingFace account\n"
                f"2. Go to [black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)\n"
                "3. Click **Agree to access repository** on the model card\n\n"
                "After that, try `/imagine` again."
            )
        # Detect download / offline / network errors
        elif any(kw in err_lower for kw in (
            "cannot find the requested files", "local cache",
            "offline mode is enabled", "connection", "timeout",
        )):
            err_str = (
                "Failed to download the **FLUX.1-schnell** model.\n\n"
                "Please check your internet connection and try `/imagine` again.\n\n"
                "If the issue persists, ensure your HuggingFace token is configured "
                "in **Settings → 🔑**."
            )
        q.put({"error": err_str})
    finally:
        # Restore offline mode
        try:
            from server.services.llm import set_offline_mode
            set_offline_mode(True)
        except Exception:
            pass
        # ALWAYS reload the LLM in the child process, even if FLUX crashed
        try:
            q.put({"model_badge": "Reloading LLM...", "model_badge_pulse": True})
            if state.model_manager and state.MODEL_NAME:
                success, name = state.model_manager.sync_load_model(state.MODEL_NAME)
                state.MODEL_NAME = name
        except Exception as reload_err:
            print(f"CRITICAL: Failed to reload LLM after FLUX: {reload_err}")
        q.put({"model_badge_restore": True})
        state.generation_lock.release()


async def flux_sse_generator(chat_id: str, q: queue.Queue,
                             title: str, action_text: str,
                             progress_text: str, alt_text: str):
    """
    Shared SSE streaming generator that consumes FLUX progress from the queue.
    """
    yield f'data: {json.dumps({"chat_id": chat_id})}\n\n'
    yield f'data: {json.dumps({"content": f"### 🎨 {title}" + chr(10) + chr(10) + "**Booting Apple Silicon GPUs...**"})}\n\n'

    def get_ascii_bar(pct, length=20):
        filled = int((pct / 100) * length)
        return "█" * filled + "░" * (length - filled)

    while True:
        try:
            msg = q.get_nowait()
            if "model_badge" in msg:
                yield f'data: {json.dumps({"model_badge": msg["model_badge"], "model_badge_pulse": msg.get("model_badge_pulse", False)})}\n\n'
            elif "model_badge_restore" in msg:
                yield f'data: {json.dumps({"model_badge_restore": True})}\n\n'
            elif "progress" in msg:
                pct = msg["progress"]
                bar = get_ascii_bar(pct)
                status = f"### 🎨 {title}\n\n**{action_text}...**\n\n`{bar}` **{pct}%**\n\n*({progress_text}...)*"
                yield f'data: {json.dumps({"replace": status})}\n\n'
            elif "image" in msg:
                img_val = msg['image']
                yield f'data: {json.dumps({"replace": f"![{alt_text}](/images/{img_val})"})}\n\n'
                break
            elif "error" in msg:
                err_val = msg['error']
                yield f'data: {json.dumps({"toast": {"message": err_val, "type": "error", "duration": 0}})}\n\n'
                yield f'data: {json.dumps({"replace": f"**Error:** {err_val}"})}\n\n'
                break
        except queue.Empty:
            await asyncio.sleep(0.2)

    yield 'data: [DONE]\n\n'
