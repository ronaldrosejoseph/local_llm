# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the server (full bootstrap: venv, deps, DB init, model load)
./start.sh

# Restart after code changes
./restart.sh

# Graceful shutdown
./stop.sh

# Run server directly (prints to stdout instead of server.log)
./venv/bin/python3 server.py

# View logs
tail -f server.log
```

There is no test suite or linter in this project.

## Architecture

This is a self-hosted, privacy-first AI chat app for macOS with Apple Silicon. **FastAPI** backend + **vanilla HTML/CSS/JS** frontend + **SQLite** storage. All inference runs locally via MLX — no cloud APIs.

```
server.py              → Entry point: sets HF_HUB_OFFLINE=1, runs uvicorn
server/app.py          → FastAPI app assembly, router includes, crash recovery, startup model load
server/state.py        → All global mutable state (model, tokenizer, document_store, generation_lock, etc.)
server/config.py       → Read/write config.json
server/db.py           → SQLite connection helper
server/models.py       → Pydantic request/response models
server/routes/         → API route handlers (chat, models, documents, config, speech)
server/services/       → Business logic (llm, rag, image_gen, memory, web_search)
```

**Frontend** uses ES modules (`type="module"`). `static/js/app.js` is the entry point — it imports all other modules and wires event listeners. `static/js/state.js` exports a shared `state` object and `elements` map that all modules import and mutate directly.

## Key Patterns

### Model loading
- VLM-first: attempts `mlx_vlm` load, falls back to `mlx_lm` (standard LLM)
- Only one model in GPU memory at a time; switching unloads the previous
- `state.IS_VLM` flag controls which generation path is used
- The `generation_lock` (threading.Lock) protects model access across concurrent requests

### Streaming (SSE)
- All generation uses Server-Sent Events with `data: {json}\n\n` lines, terminated by `data: [DONE]\n\n`
- Frontend parses SSE chunks token-by-token, renders markdown incrementally (~20fps throttle)

### Special commands (prefix-based routing in chat)
- `/web <query>` — DuckDuckGo search results injected as context
- `/imagine <prompt>` — FLUX.1 Schnell image generation (unloads LLM from VRAM first)
- `/edit <prompt>` — Image-to-image editing with FLUX using last uploaded image
- `/next` — Advances RAG pagination window

### RAG
- Documents chunked on upload (800 chars text, full file for code), embeddings via `all-MiniLM-L6-v2`
- Persisted to SQLite `documents` table as numpy BLOBs, lazy-loaded into `state.document_store`
- Dual mode: sequential page order (default) or similarity search against a user-defined topic

### Memory system
- Two-layer: rolling window (token-budget-aware, newest-first) + progressive summary (older messages incrementally summarized by LLM)
- `assemble_context()` allocates: system prompt → summary → rolling window → current message, with generation headroom reserved

### Crash recovery
- On startup, `server/app.py` checks for `.server_lifecycle` file. If present, the previous run crashed — resets active model to the safe default (`gemma-4-e2b-it-4bit`).

### Global state
All server modules import `server.state` and read/write module-level attributes directly (no getters/setters). The key variables are: `MODEL_NAME`, `model`, `tokenizer`, `processor`, `vlm_config`, `IS_VLM`, `generation_lock`, `document_store`, `rag_offsets`, `embedder_model`, `say_processes`.

### Environment
- `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set at the top of `server.py` before any HF imports
- Temporarily unset to `0` during model downloads (in `server/services/llm.py` and `server/routes/model_routes.py`)
