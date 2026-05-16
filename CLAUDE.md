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

# Completely remove the project, including HF model cache (optional)
./uninstall.sh

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
server/routes/         → API route handlers (chat, models, documents, config, speech, system_prompts)
server/services/       → Business logic
    worker.py          → Child process: loads MLX models, handles generation via stdin/stdout JSON protocol
    title_worker.py    → One-shot child process: loads a small 1B model (Llama-3.2-1B-Instruct-4bit) for title generation only — never blocks the main model
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
- For thinking models, SSE emits `thinking_start` → `thinking` (raw tokens) → `thinking_done` before regular `content` tokens. Frontend renders a collapsible "Thought" section with brain icon and pulse animation.

### Title generation (hybrid strategy)
- Titles are generated after the first turn and every 3 turns by the frontend.
- **Programmatic titles**: First message checked for `[Attached Document]`, `[Attached Image]`, `/imagine`, `/edit` — sets `Doc:`/`Image:`/`Generated:` title directly, skips LLM entirely.
- **Main model path** (non-thinking only): Uses the already-loaded worker model via `sync_nonstream_generate()` with `generation_lock` (non-blocking). Thinking models skip this to avoid waiting on chain-of-thought.
- **Title worker fallback**: One-shot subprocess (`title_worker.py`) with Llama-3.2-1B-Instruct-4bit. Used when the main model is a thinking model, is busy, or generation fails.
- Context is built tiered: full conversation (short chats), summary+latest 4 messages, or last 10 messages with role labels.

### Thinking model detection (first load)
- When a model is loaded for the first time (`has_thinking IS NULL`), `sync_detect_thinking` sends a "hi" prompt and scans the response for known end-tag patterns via regex
- 10 known end-tag patterns are checked: `</think>`, `<channel|>`, `◁/think▷`, `<|end|>`, `<unused95>`, `</thinking>`, `</reasoning>`, `</thought>`, `</answer>`, `</response>`
- Symmetric tags (same start/end, e.g. `<channel|>`) use `rfind` to locate the last occurrence (end). Closing tags (e.g. `</think>`) use `find` (first/only occurrence).
- Result persisted to `models.has_thinking` (0/1) and `models.thinking_end_tag` in the DB. Default fallback model is skipped.
- Also persists VLM/LM type (`supports_vision`) from the worker's load result during initialization.

### Thinking-aware streaming
- **Worker** (`_stream_thinking_aware`): buffers tokens until the end tag is found, then emits `thinking_start` / `thinking` / `thinking_done` before regular tokens
- **ModelManager**: `stream_generate` accepts optional `thinking_end_tag`, yields `(type, text)` tuples
- **Chat route**: looks up `has_thinking` + `thinking_end_tag` from DB before generation, passes to worker, stores `thinking_content` (raw with tags) separately from `content` (clean response) in the `messages` table
- **Frontend**: collapsible `.message-thinking` section for real-time + historical messages; typing indicator shows brain icon for thinking models; `extractThinking()` strips tags from stored thinking content for clean display

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
- Context window size is configurable via `context_window_pct` (1-100% of the model's full context). Lower = less RAM.

### Crash recovery
- **Server crash**: On startup, `server/app.py` checks for `.server_lifecycle` file. If present, the previous server run crashed — resets active model in DB to the safe default.
- **Worker OOM crash**: `ModelManager` detects child process exit (stdout EOF) or unresponsiveness (ping timeout). Automatically spawns a new child with the fallback model, updates DB, notifies frontend via SSE `model_crash` event with the crash detail (filtered from worker stderr).
- **Model load failure**: `ModelManager.load_model()` auto-falls back to the default model. The worker's specific error (e.g. quantization bit issues) is surfaced in a persistent toast.
- **stop.sh**: Kills both the server process AND any orphan worker.py processes.
- The fallback model (`gemma-4-e2b-it-4bit`) is protected from deletion in both the API and UI.

### Global state
All server modules import `server.state` and read/write module-level attributes directly (no getters/setters). The key variables are: `MODEL_NAME`, `model_manager` (ModelManager instance), `generation_lock`, `document_store`, `rag_offsets`, `embedder_model`, `say_processes`.

### Toast notifications
- **Never** use native `alert()` in the frontend. Import `showToast` from `./toast.js` instead.
- Signature: `showToast(message, type = 'info', duration = 5000)`. Types: `error`, `warning`, `success`, `info`.
- `duration = 0` makes the toast persistent (user must dismiss it manually).
- Multi-line messages: `\n` is automatically converted to `<br>` tags. Long messages scroll (max-height 200px).
- It's also exposed on `window.showToast` for use from Swift bridge callbacks.

### Generation stats
- Server tracks token count and generation time (from first token, excluding prefill). Saved to DB columns `generation_time_ms` and `token_count` on the `messages` table.
- MLX-reported `tokens_per_second` is preferred over manual timing when available.
- Frontend displays stats as `N tokens · X.Xs · Y.Y t/s` in the `.message-actions` bar. Stats persist across page reloads.
- Image generation messages (`/imagine`, `/edit`) skip the actions bar entirely.

### Model type badges
- Models show a type badge in Settings → Model Library: **VLM** (vision), **LM** (text-only), **?** (not yet loaded).
- Type is determined by the worker's actual load result (`mlx_vlm` vs `mlx_lm`), not config.json heuristics.
- `ModelManager.load_model()` persists the type to DB (`supports_vision` column). Only fills in NULL values — never overwrites confirmed types.
- Thinking models also show a 🧠 badge. `has_thinking` (NULL/0/1) and `thinking_end_tag` columns on the `models` table are populated by `sync_detect_thinking` on first load.
- The `messages` table has a `thinking_content` column for storing the raw thinking block (with tags), separate from the clean `content` response.

### Database migrations
- `init_db.py` uses `add_column_if_missing()` for safe schema evolution. New columns: `messages.thinking_content`, `models.has_thinking`, `models.thinking_end_tag`.

### Environment
- `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set at the top of `server.py` before any HF imports
- Temporarily unset to `0` during model downloads (in `server/services/llm.py` and `server/routes/model_routes.py`)
