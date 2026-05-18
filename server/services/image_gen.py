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
import traceback
import uuid

from contextlib import closing

from server import state
from server.config import load_config
from server.db import get_db_connection


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
        # Ensure HF token is active for gated model access (FLUX.1)
        try:
            from server.services.hf_auth import load_hf_token
            load_hf_token()
        except Exception:
            pass

        # Temporarily go online so mflux can download FLUX weights
        from server.services.llm import set_offline_mode
        set_offline_mode(False)

        from mflux.models.common.config import ModelConfig
        from mflux.models.flux.variants.txt2img.flux import Flux1
        from mflux.callbacks.callback import InLoopCallback
        import time

        # Unload LLM from child process to free VRAM for FLUX
        if state.model_manager:
            state.model_manager.sync_unload_model()

        os.makedirs("static/images", exist_ok=True)

        class ProgressCB(InLoopCallback):
            def call_in_loop(self, t, seed, prompt, latents, config, time_steps, **kwargs):
                if time_steps and time_steps.total > 0:
                    progress = int((time_steps.n / time_steps.total) * 100)
                    q.put({"progress": progress})

        # Signal badge: downloading/loading the image model
        q.put({"model_badge": "Downloading FLUX...", "model_badge_pulse": True})

        # Monitor HF cache blobs to report download progress (like model_routes.py)
        flux_cache_name = "models--black-forest-labs--FLUX.1-schnell"
        blobs_dir = os.path.join(os.path.expanduser("~/.cache/huggingface/hub"), flux_cache_name, "blobs")
        stop_monitor = threading.Event()

        def monitor_download():
            last_progress = -1
            while not stop_monitor.is_set():
                try:
                    if os.path.isdir(blobs_dir):
                        all_blobs = [f for f in os.listdir(blobs_dir) if not f.endswith('.lock')]
                        if all_blobs:
                            incomplete = [f for f in all_blobs if f.endswith('.incomplete')]
                            complete = len(all_blobs) - len(incomplete)
                            total = len(all_blobs)
                            if complete != last_progress:
                                last_progress = complete
                                q.put({"progress_msg": f"Downloading... ({complete}/{total} files)"})
                except Exception:
                    pass
                stop_monitor.wait(0.8)

        monitor = threading.Thread(target=monitor_download, daemon=True)
        monitor.start()

        flux = Flux1(
            model_config=ModelConfig.from_name(model_name="schnell"),
            quantize=4
        )
        flux.callbacks.register(ProgressCB())

        stop_monitor.set()
        monitor.join(timeout=1)

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
        # Walk the exception chain to find the root cause
        err_str = str(e)
        root_cause = e
        while root_cause:
            cause_str = str(root_cause)
            status_code = getattr(root_cause, 'response', None)
            status_code = getattr(status_code, 'status_code', None) if status_code else None
            # Check for 403 about gated repos in the current exception in chain
            if "403" in cause_str and ("public gated" in cause_str.lower() or "forbidden" in cause_str.lower()):
                err_str = cause_str
                break
            if status_code == 403 and "gated" in cause_str.lower():
                err_str = cause_str
                break
            root_cause = getattr(root_cause, '__cause__', None) or getattr(root_cause, '__context__', None)

        # Detect fine-grained token missing permission for public gated repos
        if "403" in err_str and ("public gated" in err_str.lower() or "forbidden" in err_str.lower()):
            err_str = (
                "Your HuggingFace token doesn't have permission to access gated repositories.\n\n"
                "If you created a **Fine-grained** token, you must enable the following permission:\n"
                "**Read access to contents of all public gated repos you can access**\n\n"
                "1. Go to your [Access Tokens Settings](https://huggingface.co/settings/tokens)\n"
                "2. Click your token to edit its permissions\n"
                "3. Under **Repository permissions**, enable the permission above\n"
                "4. Click **Save** and try `/imagine` again\n\n"
                "Alternatively, create a standard **Read** token instead of fine-grained."
            )
        # Detect gated repository / terms-not-accepted / insufficient token errors
        elif any(kw in err_str.lower() for kw in ("401", "403", "gated", "access", "terms", "repository not found")):
            err_str = (
                "HuggingFace requires you to accept the terms for **FLUX.1-schnell**\n"
                "and provide a token with gated repo access.\n\n"
                "1. Log into your HuggingFace account\n"
                f"2. Go to [black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)\n"
                "3. Click **Agree to access repository** on the model card\n"
                "4. Create a **Read** token (or fine-grained with gated repo access enabled)\n"
                "5. Add the token in **Settings → HuggingFace Token**\n\n"
                "After that, try `/imagine` again."
            )
        q.put({"error": err_str})
    finally:
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
            elif "progress_msg" in msg:
                dl_msg = msg["progress_msg"]
                yield f'data: {json.dumps({"replace": f"### 🎨 {title}\n\n{dl_msg}"})}\n\n'
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
    