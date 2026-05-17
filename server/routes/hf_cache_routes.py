"""
App data cleanup — get size and delete local caches (HF models + app data).
Only use when removing the app.
"""

import os
import shutil

from fastapi import APIRouter

router = APIRouter()

HF_CACHE_DIR = os.path.expanduser("~/.cache/huggingface")
APP_DATA_DIR = os.path.expanduser("~/Library/Application Support/Local LLM")

_DIRS_TO_DELETE = [HF_CACHE_DIR, APP_DATA_DIR]


def _dir_size(path: str) -> int:
    if not os.path.exists(path):
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


@router.get("/api/hf-cache/info")
def hf_cache_info():
    total = 0
    paths = {}
    for d in _DIRS_TO_DELETE:
        sz = _dir_size(d)
        paths[d] = sz
        total += sz
    return {"size_bytes": total, "paths": paths}


@router.delete("/api/hf-cache")
def delete_hf_cache():
    total_deleted = 0
    errors = []

    for d in _DIRS_TO_DELETE:
        if not os.path.exists(d):
            continue
        sz = _dir_size(d)
        try:
            shutil.rmtree(d)
            total_deleted += sz
        except OSError as e:
            errors.append(f"{d}: {e}")

    if errors:
        return {"error": "; ".join(errors), "deleted_bytes": total_deleted}

    return {"deleted_bytes": total_deleted, "message": "All app data and model cache deleted."}
