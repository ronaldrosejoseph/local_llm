"""
Global mutable state and constants.

All modules that need to read or modify runtime state should import this module
and access/mutate attributes directly (e.g. `state.MODEL_NAME = new_name`).
"""

import threading

# --- Constants ---
DEFAULT_MODEL = "mlx-community/gemma-4-e2b-it-4bit"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB hard limit to prevent OOM on large uploads

# --- Model State ---
MODEL_NAME = None          # Current model's HF repo ID
model_manager = None       # ModelManager instance (runs inference in child process)

# --- Concurrency ---
generation_lock = threading.Lock()  # Protects model during generation, switching, and FLUX

# --- RAG State ---
document_store = {}        # chat_id -> [{type, text, emb, ...}]  backed by documents DB table
rag_offsets = {}           # chat_id -> int offset for pagination
embedder_model = None      # SentenceTransformer instance (lazy loaded)

# --- TTS State ---
say_processes = set()      # Tracked subprocess.Popen objects for TTS
