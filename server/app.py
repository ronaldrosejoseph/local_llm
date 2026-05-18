"""
FastAPI application assembly.

Creates the FastAPI app, includes all routers, mounts static files,
manages the model worker child process lifecycle, and handles crash recovery.
"""

import os
import sys
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.routes.chat import router as chat_router
from server.routes.model_routes import router as model_router
from server.routes.documents import router as documents_router
from server.routes.config_routes import router as config_router
from server.routes.speech import router as speech_router
from server.routes.system_prompt_routes import router as system_prompt_router
from server.routes.hf_token_routes import router as hf_token_router
from server.routes.hf_cache_routes import router as hf_cache_router

app = FastAPI()

# Include all route modules
app.include_router(chat_router)
app.include_router(model_router)
app.include_router(documents_router)
app.include_router(config_router)
app.include_router(speech_router)
app.include_router(system_prompt_router)
app.include_router(hf_token_router)
app.include_router(hf_cache_router)

# Server Lifecycle Check (Crash Recovery)
LIFECYCLE_FILE = ".server_lifecycle"

if os.path.exists(LIFECYCLE_FILE):
    print(f"Server: {LIFECYCLE_FILE} exists. Previous run may have crashed. Recovering...", file=sys.stderr)
    from server.db import reset_to_default_model
    reset_to_default_model()

# Mark as running
with open(LIFECYCLE_FILE, "w") as f:
    f.write("running")


@app.on_event("startup")
async def startup():
    """Initialize the model manager, load HF token, and load the active model."""
    from server.services.model_manager import ModelManager
    from server.services.hf_auth import load_hf_token
    from server import state

    # Load HF token from keyring at startup
    load_hf_token()

    manager = ModelManager()
    state.model_manager = manager
    await manager.start()
    print(f"Server: model worker ready (model={state.MODEL_NAME})", file=sys.stderr)


@app.on_event("shutdown")
async def shutdown():
    """Stop the model worker and clean up all resources gracefully."""
    import warnings
    # Suppress harmless loky semaphore warnings (joblib/parallel cleanup)
    warnings.filterwarnings("ignore", message=".*leaked semaphore.*")
    warnings.filterwarnings("ignore", message=".*unclosed file.*")
    from server import state

    # Stop the model worker child process
    if state.model_manager:
        await state.model_manager.stop()

    # Terminate any running TTS processes
    import signal
    for p in list(state.say_processes):
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    state.say_processes.clear()

    # Clean up multiprocessing resources (prevents semaphore leak warnings)
    try:
        import multiprocessing.resource_tracker
        # Unregister any remaining tracked resources
        tracker = getattr(multiprocessing.resource_tracker, '_resource_tracker', None)
        if tracker is not None and hasattr(tracker, '_stop'):
            tracker._stop()
    except Exception:
        pass

    # Remove lifecycle file on clean shutdown
    if os.path.exists(LIFECYCLE_FILE):
        try:
            os.remove(LIFECYCLE_FILE)
        except OSError:
            pass


# Mount user-generated content directories from the writable data dir.
# These must be mounted BEFORE the root static mount so they take priority.
from server.config import get_static_dir
_user_static = get_static_dir()
os.makedirs(os.path.join(_user_static, "images"), exist_ok=True)
os.makedirs(os.path.join(_user_static, "uploads"), exist_ok=True)
app.mount("/images", StaticFiles(directory=os.path.join(_user_static, "images")), name="user_images")
app.mount("/uploads", StaticFiles(directory=os.path.join(_user_static, "uploads")), name="user_uploads")

# Serve frontend static assets (HTML/JS/CSS) from the project directory
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
