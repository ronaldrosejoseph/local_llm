"""
Configuration management — load/save generation parameters from config.json.
"""

import os
import json

CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "max_tokens": 8192,
    "temperature": 0.3,
    "top_p": 0.9,
    "repetition_penalty": 1.1,
    "pdf_text_pages_per_batch": 50,
    "pdf_image_pages_per_batch": 5,
    "image_generation_resolution": "720x720",
    "rolling_window_max_tokens": 4096, # Max tokens for recent messages before falling back to summary
    "summary_max_tokens": 400,     # Max tokens for progressive chat summary
}


def load_config() -> dict:
    """Load config from disk, falling back to defaults for any missing keys."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
