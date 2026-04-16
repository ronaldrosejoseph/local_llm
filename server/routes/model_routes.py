"""
Model management routes — list, add, switch, and delete models.
"""

import os
import sqlite3
import queue
import threading
import asyncio
import json
import shutil

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from contextlib import closing
from huggingface_hub import HfApi

from server import state
from server.db import get_db_connection
from server.models import ModelAdd
from server.services.llm import load_active_model, is_model_cached

router = APIRouter()


@router.get("/api/models")
def get_models():
    with closing(get_db_connection()) as conn:
        models = conn.execute("SELECT id, name, active FROM models").fetchall()
    return [{"id": m["id"], "name": m["name"], "active": bool(m["active"])} for m in models]


@router.post("/api/models")
async def add_model(model_data: ModelAdd):
    """SSE stream that verifies, downloads, and adds a new model."""
    if "mlx-community" not in model_data.name:
        raise HTTPException(status_code=400, detail="Model must be from mlx-community")

    # Check if exists in DB first
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT name FROM models WHERE name = ?", (model_data.name,)).fetchone()
        if row:
            raise HTTPException(status_code=400, detail="Model already in library")

    # HF Cache path detection
    safe = model_data.name.replace("/", "--")
    cache_dir = os.path.join(os.path.expanduser("~/.cache/huggingface/hub"), f"models--{safe}")
    blobs_dir = os.path.join(cache_dir, "blobs")
    snapshots_dir = os.path.join(cache_dir, "snapshots")
    is_cached = os.path.isdir(snapshots_dir)

    result_q: queue.Queue = queue.Queue()

    def download_thread():
        try:
            # Temporarily enable networking for download
            import huggingface_hub.constants
            os.environ["HF_HUB_OFFLINE"] = "0"
            os.environ["TRANSFORMERS_OFFLINE"] = "0"
            huggingface_hub.constants.HF_HUB_OFFLINE = False

            # 1. Verify model existence on Hugging Face
            api = HfApi()
            api.model_info(repo_id=model_data.name)

            # 2. Trigger download (if not cached)
            if not is_cached:
                from huggingface_hub import snapshot_download
                snapshot_download(repo_id=model_data.name, local_files_only=False)

            # 3. Add to DB
            with closing(get_db_connection()) as conn:
                try:
                    conn.execute("INSERT INTO models (name) VALUES (?)", (model_data.name,))
                    conn.commit()
                except sqlite3.IntegrityError:
                    pass

            # Restore offline mode for safety
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            huggingface_hub.constants.HF_HUB_OFFLINE = True

            result_q.put({"status": "ready", "model": model_data.name.split("/")[-1], "full": model_data.name})
        except Exception as e:
            # Restore offline mode even on error
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            try:
                import huggingface_hub.constants
                huggingface_hub.constants.HF_HUB_OFFLINE = True
            except:
                pass
            result_q.put({"status": "error", "message": str(e)})

    threading.Thread(target=download_thread, daemon=True).start()

    async def status_stream():
        yield f"data: {json.dumps({'status': 'checking', 'message': 'Verifying model on Hugging Face...'})}\n\n"

        last_progress = -1
        while True:
            try:
                msg = result_q.get_nowait()
                yield f"data: {json.dumps(msg)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except queue.Empty:
                pass

            # Report download progress by counting blobs
            try:
                if os.path.isdir(blobs_dir):
                    all_blobs = [f for f in os.listdir(blobs_dir) if not f.endswith('.lock')]
                    if all_blobs:
                        incomplete = [f for f in all_blobs if f.endswith('.incomplete')]
                        complete = len(all_blobs) - len(incomplete)
                        total = len(all_blobs)

                        percent = int((complete / total) * 100) if total > 0 else 0
                        if percent != last_progress:
                            last_progress = percent
                            progress_msg = f"Downloading... ({complete}/{total} files)"
                            yield f"data: {json.dumps({'status': 'downloading', 'message': progress_msg, 'percent': percent, 'complete': complete, 'total': total})}\n\n"
            except Exception:
                pass

            await asyncio.sleep(0.8)

    return StreamingResponse(status_stream(), media_type="text/event-stream")


@router.post("/api/models/active")
async def set_active_model(model_data: ModelAdd):
    """SSE stream that reports download/load progress while switching models."""
    prev_model_name = state.MODEL_NAME

    # Verify model exists in DB first
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT name FROM models WHERE name = ?", (model_data.name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")

    # Detect cache state BEFORE starting the load thread.
    safe = model_data.name.replace("/", "--")
    cache_dir = os.path.join(os.path.expanduser("~/.cache/huggingface/hub"), f"models--{safe}")
    blobs_dir = os.path.join(cache_dir, "blobs")
    snapshots_dir = os.path.join(cache_dir, "snapshots")
    is_cached = os.path.isdir(snapshots_dir)

    result_q: queue.Queue = queue.Queue()

    def load_thread():
        # Acquire generation lock to prevent model switch during active generation
        state.generation_lock.acquire()
        try:
            # Update DB active flag optimistically (load_active_model will correct it on failure)
            with closing(get_db_connection()) as conn:
                conn.execute("UPDATE models SET active = 0")
                conn.execute("UPDATE models SET active = 1 WHERE name = ?", (model_data.name,))
                conn.commit()

            # Use the centralized helper to handle VLM vs LLM and global state
            success, actual_name = load_active_model(override_name=model_data.name)

            payload = {
                "status": "ready",
                "model": actual_name.split("/")[-1],
                "full": actual_name
            }
            if not success:
                payload["fallback"] = True
                payload["requested"] = model_data.name
                payload["error"] = "Model failed to load and was reverted to default."
            result_q.put(payload)
        except Exception as e:
            # Restore previous active model in DB
            try:
                with closing(get_db_connection()) as conn:
                    conn.execute("UPDATE models SET active = 0")
                    if prev_model_name:
                        conn.execute("UPDATE models SET active = 1 WHERE name = ?", (prev_model_name,))
                    conn.commit()
            except Exception:
                pass
            result_q.put({"status": "error", "message": f"Critical error during model switch: {str(e)}"})
        finally:
            state.generation_lock.release()

    threading.Thread(target=load_thread, daemon=True).start()

    async def status_stream():
        loading_notified = is_cached

        if is_cached:
            yield f"data: {json.dumps({'status': 'loading', 'message': 'Loading model into memory...'})}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'downloading', 'message': 'Downloading model files...'})}\n\n"

        while True:
            # Check if the load thread finished
            try:
                msg = result_q.get_nowait()
                yield f"data: {json.dumps(msg)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except queue.Empty:
                pass

            if not loading_notified:
                # Report download progress by counting blobs
                try:
                    if os.path.isdir(blobs_dir):
                        all_blobs = [f for f in os.listdir(blobs_dir) if not f.endswith('.lock')]
                        incomplete = [f for f in all_blobs if f.endswith('.incomplete')]
                        complete = len(all_blobs) - len(incomplete)
                        total = len(all_blobs)
                        if total > 0:
                            progress_msg = f"Downloading... ({complete}/{total} files)"
                            yield f"data: {json.dumps({'status': 'downloading', 'message': progress_msg})}\n\n"
                        # Transition to "loading" once no incomplete files remain
                        if total > 0 and len(incomplete) == 0:
                            loading_notified = True
                            yield f"data: {json.dumps({'status': 'loading', 'message': 'Loading model into memory...'})}\n\n"
                except Exception:
                    pass

            await asyncio.sleep(0.8)

    return StreamingResponse(status_stream(), media_type="text/event-stream")


@router.delete("/api/models/{model_name:path}")
def delete_model(model_name: str):
    # Refuse to delete the currently loaded model
    if model_name == state.MODEL_NAME:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the active model. Switch to another model first."
        )

    # Remove from DB
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT name FROM models WHERE name = ?", (model_name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found in database.")
        conn.execute("DELETE FROM models WHERE name = ?", (model_name,))
        conn.commit()

    # Delete from HuggingFace hub cache.
    hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
    safe_name = model_name.replace("/", "--")
    cache_dir = os.path.join(hf_cache, f"models--{safe_name}")

    deleted_from_disk = False
    if os.path.isdir(cache_dir):
        try:
            shutil.rmtree(cache_dir)
            deleted_from_disk = True
            print(f"Deleted model cache: {cache_dir}")
        except Exception as e:
            print(f"Warning: could not delete cache at {cache_dir}: {e}")
    else:
        print(f"No cache dir found at {cache_dir} — only removed from DB.")

    return {
        "status": "ok",
        "model": model_name,
        "deleted_from_disk": deleted_from_disk,
        "cache_path": cache_dir,
    }
