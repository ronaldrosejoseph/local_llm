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

app = FastAPI()

# Include all route modules
app.include_router(chat_router)
app.include_router(model_router)
app.include_router(documents_router)
app.include_router(config_router)
app.include_router(speech_router)

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
    """Initialize the model manager and load the active model."""
    from server.services.model_manager import ModelManager
    from server import state

    manager = ModelManager()
    state.model_manager = manager
    await manager.start()
    print(f"Server: model worker ready (model={state.MODEL_NAME})", file=sys.stderr)


@app.on_event("shutdown")
async def shutdown():
    """Stop the model worker gracefully."""
    from server import state

    if state.model_manager:
        await state.model_manager.stop()

    # Remove lifecycle file on clean shutdown
    if os.path.exists(LIFECYCLE_FILE):
        try:
            os.remove(LIFECYCLE_FILE)
        except OSError:
            pass


# Serve static files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
