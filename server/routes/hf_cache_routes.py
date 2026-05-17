"""
HF cache management — get size and delete the local HuggingFace model cache.
"""

import os
import shutil

from fastapi import APIRouter

router = APIRouter()

HF_CACHE_DIR = os.path.expanduser("~/.cache/huggingface")


def _get_cache_size_bytes() -> int:
    """Walk the HF cache directory and sum file sizes."""
    if not os.path.exists(HF_CACHE_DIR):
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(HF_CACHE_DIR):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


@router.get("/api/hf-cache/info")
def hf_cache_info():
    size_bytes = _get_cache_size_bytes()
    return {"size_bytes": size_bytes, "path": HF_CACHE_DIR}


@router.delete("/api/hf-cache")
def delete_hf_cache():
    if not os.path.exists(HF_CACHE_DIR):
        return {"deleted_bytes": 0, "message": "No cache directory found."}

    size_before = _get_cache_size_bytes()

    try:
        shutil.rmtree(HF_CACHE_DIR)
    except OSError as e:
        return {"error": f"Failed to delete cache: {e}"}

    return {"deleted_bytes": size_before, "message": "Model cache deleted."}
