"""
Model cache helpers and model-type detection.

Model loading is now handled by the worker child process
(server/services/worker.py), managed by ModelManager.
"""

import os
import json
import glob


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


def detect_model_type(model_name: str) -> str:
    """Detect whether a model is a vision-language model (VLM) or text-only (LM).

    Reads the model's config.json from the local HuggingFace cache.
    Falls back to HuggingFace Hub if not cached locally.
    Returns 'vlm', 'lm', or 'unknown' (if config can't be read).
    """
    vision_keys = {
        "vision_config", "image_processor", "image_token_id",
        "mm_projector", "vision_tower", "visual",
    }
    vision_keywords = (
        "vision", "vlm", "_vl_", "-vl-",
    )

    config = _load_model_config(model_name)
    if config is None:
        config = _fetch_remote_config(model_name)
    if config is None:
        return "unknown"

    # Check 1: top-level vision keys
    if vision_keys & set(config.keys()):
        return "vlm"

    # Check 2: architecture name or model_type
    for field in ("architectures", "model_type"):
        values = config.get(field, [])
        if isinstance(values, str):
            values = [values]
        for v in values:
            if any(kw in v.lower() for kw in vision_keywords):
                return "vlm"

    # Check 3: nested text_config (Gemma 4 style)
    if "text_config" in config and isinstance(config["text_config"], dict):
        if vision_keys & set(config["text_config"].keys()):
            return "vlm"

    return "lm"


def _load_model_config(model_name: str) -> dict | None:
    """Load config.json from the local HF cache for a given model name."""
    safe = model_name.replace("/", "--")
    cache_dir = os.path.join(
        os.path.expanduser("~/.cache/huggingface/hub"), f"models--{safe}"
    )
    snapshots_dir = os.path.join(cache_dir, "snapshots")
    if not os.path.isdir(snapshots_dir):
        return None

    # Find config.json in any snapshot subdirectory
    pattern = os.path.join(snapshots_dir, "*", "config.json")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    try:
        with open(candidates[0], "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _fetch_remote_config(model_name: str) -> dict | None:
    """Download config.json from HuggingFace Hub as a fallback."""
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id=model_name, filename="config.json")
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None
