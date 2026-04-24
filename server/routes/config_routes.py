"""
Config routes — get and update generation parameters.
"""

from fastapi import APIRouter

from server.config import load_config, save_config
from server.models import ConfigUpdate

router = APIRouter()


@router.get("/api/config")
def get_config():
    return load_config()


@router.patch("/api/config")
def update_config(data: ConfigUpdate):
    cfg = load_config()
    if data.max_tokens is not None:
        cfg["max_tokens"] = max(256, min(131072, int(data.max_tokens)))
    if data.temperature is not None:
        cfg["temperature"] = round(max(0.0, min(2.0, data.temperature)), 2)
    if data.top_p is not None:
        cfg["top_p"] = round(max(0.0, min(1.0, data.top_p)), 2)
    if data.repetition_penalty is not None:
        cfg["repetition_penalty"] = round(max(1.0, min(1.5, data.repetition_penalty)), 2)
    if data.pdf_text_pages_per_batch is not None:
        cfg["pdf_text_pages_per_batch"] = max(1, int(data.pdf_text_pages_per_batch))
    if data.pdf_image_pages_per_batch is not None:
        cfg["pdf_image_pages_per_batch"] = max(1, int(data.pdf_image_pages_per_batch))
    if data.image_generation_resolution is not None:
        cfg["image_generation_resolution"] = data.image_generation_resolution
    # Memory system
    if data.memory_top_k is not None:
        cfg["memory_top_k"] = max(0, min(10, int(data.memory_top_k)))
    if data.memory_max_tokens is not None:
        cfg["memory_max_tokens"] = max(0, min(2000, int(data.memory_max_tokens)))
    if data.summary_max_tokens is not None:
        cfg["summary_max_tokens"] = max(0, min(1000, int(data.summary_max_tokens)))
    save_config(cfg)
    return cfg
