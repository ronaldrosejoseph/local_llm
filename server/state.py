"""
Global mutable state and constants.

All modules that need to read or modify runtime state should import this module
and access/mutate attributes directly (e.g. `state.model = new_model`).
"""

# --- Constants ---
DEFAULT_MODEL = "mlx-community/gemma-4-e2b-it-4bit"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB hard limit to prevent OOM on large uploads

# --- Model State ---
MODEL_NAME = None          # Current model's HF repo ID
model = None               # Loaded MLX model object
tokenizer = None           # Loaded tokenizer
processor = None           # VLM processor (None for text-only models)
vlm_config = None          # Cached VLM config dict
IS_VLM = False             # True if current model is a Vision model

# --- RAG State ---
document_store = {}        # chat_id -> [{type, text, emb, ...}]  (IN-MEMORY, volatile)
rag_offsets = {}           # chat_id -> int offset for pagination
embedder_model = None      # SentenceTransformer instance (lazy loaded)

# --- TTS State ---
say_processes = set()      # Tracked subprocess.Popen objects for TTS
