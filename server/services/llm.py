"""
Model cache helpers and model-type detection.

Model loading is now handled by the worker child process
(server/services/worker.py), managed by ModelManager.
"""

import os


def set_offline_mode(offline: bool) -> None:
    """Toggle HF offline environment variables."""
    os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "1" if offline else "0"
    try:
        import huggingface_hub.constants as hfc
        hfc.HF_HUB_OFFLINE = offline
    except ImportError:
        pass
