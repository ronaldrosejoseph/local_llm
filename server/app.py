"""
FastAPI application assembly.

Creates the FastAPI app, includes all routers, mounts static files,
and triggers the initial model load at import time.
"""

import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.routes.chat import router as chat_router
from server.routes.model_routes import router as model_router
from server.routes.documents import router as documents_router
from server.routes.config_routes import router as config_router
from server.routes.speech import router as speech_router
from server.services.llm import load_active_model

app = FastAPI()

# Include all route modules
app.include_router(chat_router)
app.include_router(model_router)
app.include_router(documents_router)
app.include_router(config_router)
app.include_router(speech_router)

# Initial model load at startup
load_active_model()

# Serve static files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
