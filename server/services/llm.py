"""
Model cache helpers.

Model loading is now handled by the worker child process
(server/services/worker.py), managed by ModelManager.
"""

import os


def is_model_cached(model_name: str) -> bool:
    """Check whether a model exists in the local HuggingFace cache."""
    safe = model_name.replace("/", "--")
    cache_dir = os.path.join(
        os.path.expanduser("~/.cache/huggingface/hub"), f"models--{safe}"
    )
    snapshots_dir = os.path.join(cache_dir, "snapshots")
    return os.path.isdir(snapshots_dir)


def set_offline_mode(offline: bool):
    """Toggle HF offline environment variables."""
    os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "1" if offline else "0"
    try:
        import huggingface_hub.constants as hfc
        hfc.HF_HUB_OFFLINE = offline
    except ImportError:
        pass
