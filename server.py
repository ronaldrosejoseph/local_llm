"""
Entry point for the Local LLM server.

Sets HuggingFace offline environment variables BEFORE any HF library imports,
then imports and runs the FastAPI application.
"""

import os

# MUST be before importing anything from HF to ensure libraries like transformers respect it
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from server.app import app  # noqa: E402 — import after env setup

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
