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
server/app.py          → FastAPI app assembly, router includes, crash recovery, ModelManager init
server/state.py        → All global mutable state (MODEL_NAME, model_manager, generation_lock, document_store, etc.)
server/config.py       → Read/write config.json
server/db.py           → SQLite connection helper
server/models.py       → Pydantic request/response models
server/routes/         → API route handlers (chat, models, documents, config, speech)
server/services/       → Business logic
    worker.py          → Child process: loads MLX models, handles generation via stdin/stdout JSON protocol
    model_manager.py   → Parent-side manager: spawns/manages worker, proxies commands, crash recovery
    llm.py             → Cache helpers (is_model_cached, set_offline_mode)
    rag.py             → Document embedding, chunking, retrieval
    image_gen.py       → FLUX pipeline (unloads/reloads child model via ModelManager)
    memory.py          → Context assembly, progressive summarization
    web_search.py      → DuckDuckGo scraping
```

**Frontend** uses ES modules (`type="module"`). `static/js/app.js` is the entry point — it imports all other modules and wires event listeners. `static/js/state.js` exports a shared `state` object and `elements` map that all modules import and mutate directly.

## Key Patterns

### Model inference (child process)
- MLX model inference runs in a separate child process (`server/services/worker.py`) to isolate OOM crashes
- Parent and child communicate via JSON-line protocol over stdin/stdout
- `server/services/model_manager.py` (`ModelManager`) manages the child lifecycle, proxies commands, and handles crash recovery
- If the child process dies (OOM), the parent detects it, restarts the child, and loads the fallback model (`gemma-4-e2b-it-4bit`)
- The parent updates the DB and frontend UI to reflect the fallback
- VLM-first loading: worker attempts `mlx_vlm.load()`, falls back to `mlx_lm.load()`
- Only one child process at a time; switching models reuses the same process
- `state.model_manager.is_vlm` controls which generation path is used
- The `generation_lock` (threading.Lock) serializes all access to the child process

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
- **Server crash**: On startup, `server/app.py` checks for `.server_lifecycle` file. If present, the previous server run crashed — resets active model in DB to the safe default.
- **Worker OOM crash**: `ModelManager` detects child process exit (stdout EOF) or unresponsiveness (ping timeout). Automatically spawns a new child with the fallback model, updates DB, notifies frontend via SSE `model_crash` event.
- **stop.sh**: Kills both the server process AND any orphan worker.py processes.

### Global state
All server modules import `server.state` and read/write module-level attributes directly (no getters/setters). The key variables are: `MODEL_NAME`, `model_manager` (ModelManager instance), `generation_lock`, `document_store`, `rag_offsets`, `embedder_model`, `say_processes`.

### Toast notifications
- **Never** use native `alert()` in the frontend. Import `showToast` from `./toast.js` instead.
- Signature: `showToast(message, type = 'info', duration = 5000)`. Types: `error`, `warning`, `success`, `info`.
- It's also exposed on `window.showToast` for use from Swift bridge callbacks.

### Environment
- `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set at the top of `server.py` before any HF imports
- Temporarily unset to `0` during model downloads (in `server/services/llm.py` and `server/routes/model_routes.py`)
