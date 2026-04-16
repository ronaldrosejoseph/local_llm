"""
LLM / VLM model loading and management.

Handles loading models from HuggingFace cache via mlx_lm or mlx_vlm,
managing VRAM lifecycle, and falling back to defaults on failure.
"""

import os
import gc

import mlx_lm
from mlx_lm import load
import mlx_vlm
from mlx_vlm.utils import load_config as load_vlm_config
import mlx.core as mx
from contextlib import closing

from server import state
from server.db import get_db_connection


def is_model_cached(model_name: str) -> bool:
    safe = model_name.replace("/", "--")
    cache_dir = os.path.join(os.path.expanduser("~/.cache/huggingface/hub"), f"models--{safe}")
    snapshots_dir = os.path.join(cache_dir, "snapshots")
    return os.path.isdir(snapshots_dir)


def load_active_model(override_name: str = None) -> tuple[bool, str]:
    """Loads the specified or default model. Returns (success, actual_model_name)."""

    if override_name:
        state.MODEL_NAME = override_name
    else:
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT name FROM models WHERE active = 1").fetchone()

        if not row:
            state.MODEL_NAME = state.DEFAULT_MODEL
        else:
            state.MODEL_NAME = row["name"]

    print(f"Loading model {state.MODEL_NAME}...")

    # Close previous model and free VRAM
    state.model = None
    state.tokenizer = None
    state.processor = None
    state.vlm_config = None
    gc.collect()
    mx.clear_cache()

    # Determine if we should force local-only
    use_local = is_model_cached(state.MODEL_NAME)
    if not use_local:
        # If not cached, we MUST allow networking to let it download if it fails over
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "0"
        print(f"Enabling networking for {state.MODEL_NAME} (not cached)")
    else:
        # Ensure offline mode is enforced
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    try:
        # Step 1: Attempt to load as a Vision model (VLM)
        print(f"Checking if {state.MODEL_NAME} is a Vision model (mlx_vlm)...")
        # mlx_vlm.load doesn't always handle local_files_only correctly, use env-var approach
        state.model, state.processor = mlx_vlm.load(state.MODEL_NAME)
        state.tokenizer = state.processor.tokenizer
        state.vlm_config = load_vlm_config(state.MODEL_NAME)
        state.IS_VLM = True
        print(f"Model {state.MODEL_NAME} loaded successfully as VLM.")
        return True, state.MODEL_NAME
    except Exception as e_vlm:
        # Step 2: If VLM load fails, attempt as a standard LLM
        print(f"Not a VLM or mlx_vlm failed ({e_vlm}). Trying as standard LLM (mlx_lm)...")
        try:
            state.model, state.tokenizer = load(state.MODEL_NAME)
            state.processor = None
            state.vlm_config = None
            state.IS_VLM = False
            print(f"Model {state.MODEL_NAME} loaded successfully as standard LLM.")
            return True, state.MODEL_NAME
        except Exception as e_lm:
            # Step 3: Total Load Failure
            error_msg = str(e_lm)
            print(f"Error loading model {state.MODEL_NAME}: {error_msg}")

            # If the active model fails, try to load the default
            if state.MODEL_NAME != state.DEFAULT_MODEL:
                print(f"Falling back to default model: {state.DEFAULT_MODEL}")
                # Update DB to reflect fallback so UI and future loads are correct
                with closing(get_db_connection()) as conn:
                    conn.execute("UPDATE models SET active = 0")
                    conn.execute("UPDATE models SET active = 1 WHERE name = ?", (state.DEFAULT_MODEL,))
                    conn.commit()

                # Recursive call to load the default model
                _, final_name = load_active_model(override_name=state.DEFAULT_MODEL)
                return False, final_name
            else:
                print("CRITICAL: Both active and default models failed to load. Server will be non-functional.")
                state.model, state.tokenizer, state.processor = None, None, None
                return False, state.MODEL_NAME
