# AGENTS.md — Project Context for AI Assistants

## Project Overview

**Local LLM Chat** is a self-hosted, privacy-first AI chat application for macOS with Apple Silicon. It provides a ChatGPT-style web interface backed by local model inference using Apple's MLX framework. Everything runs on-device — no cloud APIs, no telemetry.

**Stack:** FastAPI (Python) backend + Vanilla HTML/CSS/JS frontend + SQLite storage.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Browser (Frontend)                │
│   static/index.html + js/*.js + style.css           │
│   Libraries: Lucide Icons, Marked.js, DOMPurify     │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP + SSE (Server-Sent Events)
┌──────────────────────▼──────────────────────────────┐
│                FastAPI Server (server/)              │
│                                                     │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Chat &  │ │ Model   │ │ Document │ │ Image    │ │
│  │ Gen     │ │ Mgmt    │ │ Upload & │ │ Gen      │ │
│  │ (LLM/  │ │ (load/  │ │ RAG      │ │ (FLUX)   │ │
│  │  VLM)   │ │ switch) │ │          │ │          │ │
│  └────┬────┘ └────┬────┘ └────┬─────┘ └────┬─────┘ │
│       │           │           │             │       │
│  ┌────▼───────────▼───────────▼─────────────▼────┐  │
│  │      Global State (server/state.py)            │  │
│  │  model, tokenizer, processor, document_store   │  │
│  └────────────────────┬──────────────────────────┘  │
└───────────────────────┼─────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   ┌─────────┐   ┌───────────┐   ┌───────────────┐
   │ SQLite  │   │ HF Cache  │   │ MLX / PyTorch │
   │ (chats, │   │ (~/.cache/ │   │ (Apple GPU)   │
   │ models) │   │ huggingface│   │               │
   └─────────┘   └───────────┘   └───────────────┘
```

---

## File Map

### Backend

| File | Purpose |
|------|---------|
| `server.py` | **Entry point** — sets HF offline env vars, imports app from server package, runs uvicorn. ~15 lines. |
| `server/__init__.py` | Package marker. |
| `server/app.py` | FastAPI app creation, router includes, static file mount, ModelManager init, shutdown handler. |
| `server/state.py` | All global mutable state: MODEL_NAME, model_manager (ModelManager), document_store, generation_lock, etc. |
| `server/config.py` | Config load/save from `config.json` with defaults. |
| `server/db.py` | SQLite connection helper. |
| `server/models.py` | Pydantic request/response models (Message, ChatCreate, ConfigUpdate, etc.). |
| `server/services/llm.py` | Cache helpers: `is_model_cached()`, `set_offline_mode()`. |
| `server/services/worker.py` | **Child process** — standalone script that loads MLX models and runs generation. Communicates with parent via JSON-line stdin/stdout protocol. |
| `server/services/title_worker.py` | **Title worker** — one-shot child process that loads a small 1B model (Llama-3.2-1B-Instruct-4bit) solely for title generation. Reads JSON prompt from stdin, writes title to stdout, exits. Never blocks the main model. |
| `server/services/model_manager.py` | **ModelManager** — parent-side process manager. Spawns/manages worker, proxies generation commands, health checks, crash recovery. |
| `server/services/rag.py` | Embedder loading, PDF-to-image, document chunking, semantic retrieval, vision PDF pagination. |
| `server/services/image_gen.py` | Shared FLUX pipeline for `/imagine` and `/edit` (deduplicated). |
| `server/services/memory.py` | Hybrid memory system: token-aware rolling window, progressive summarization, cross-chat vector retrieval. |
| `server/services/web_search.py` | DuckDuckGo scraping + weather widget. |
| `server/routes/chat.py` | Chat CRUD + main streaming generation endpoint. |
| `server/routes/model_routes.py` | Model list, add (SSE download), switch (SSE load), delete. |
| `server/routes/documents.py` | Document/image upload for RAG. |
| `server/routes/config_routes.py` | Generation config GET/PATCH. |
| `server/routes/speech.py` | TTS via macOS `say` command. |
| `server/routes/system_prompt_routes.py` | System prompt template CRUD: search, save, update, delete reusable personas. |
| `server/routes/hf_token_routes.py` | HF token management: verify, save (to Keychain), delete, status check. |
| `server/routes/hf_cache_routes.py` | App data cleanup: get combined size of HF cache + app data dir, delete both. Multi-step confirmation in Settings. |
| `server/services/hf_auth.py` | Secure token storage via `keyring` (macOS Keychain). `verify_token()` uses direct HTTP to bypass offline mode. |

### Frontend

| File | Purpose |
|------|---------|
| `static/index.html` | Single-page HTML shell. |
| `static/style.css` | Complete styling. Dark theme, responsive, glassmorphism. ~1300 lines. |
| `static/js/app.js` | **Entry point** — imports all modules, DOMContentLoaded init, event wiring, window globals, keyboard shortcut handler (`initKeyboardShortcuts`), drag-and-drop upload overlay (`initDragAndDrop`). |
| `static/js/state.js` | Shared state object + DOM element references (includes `dropOverlay` for drag-and-drop). |
| `static/js/utils.js` | Markdown rendering (Marked + DOMPurify), clipboard, scroll management. |
| `static/js/chat.js` | sendMessage with SSE parsing, appendMessage, typing indicator, message editing (`editMessage`), response regeneration (`regenerateMessage`). |
| `static/js/sidebar.js` | Chat history, navigation, new/delete chat, modals, sidebar toggle. |
| `static/js/models.js` | Model loading, adding (SSE download), switching (SSE load). |
| `static/js/settings.js` | Settings modal, config sliders, model library UI. |
| `static/js/documents.js` | File upload handler, attachment pill UI. |
| `static/js/speech.js` | TTS (server API), speech-to-text (Web Speech API). |
| `static/js/system_prompt.js` | System prompt management — per-chat editing, template search, save/load/delete reusable personas. |
| `static/js/toast.js` | Toast notification component — `showToast(msg, type, duration)`. Types: error/warning/success/info. Use this instead of native `alert()`. |
| `static/uploads/` | Uploaded documents and images (runtime, gitignored). |
| `static/images/` | Generated images from FLUX (runtime, gitignored). |

### Other

| File | Purpose |
|------|---------|
| `init_db.py` | Database schema creation and migrations. Run once on first startup. |
| `config.json` | Runtime config (temperature, max_tokens, top_p, etc.). Read/written by server. In bundled mode, stored in `~/Library/Application Support/Local LLM/` (writable data directory) and seeded from the bundle's default on first launch. |
| `requirements.txt` | Python dependencies. |
| `start.sh` | Bootstrap script: installs Homebrew/Python if needed, creates venv, installs deps, inits DB, launches server. Detects bundled-vs-dev mode automatically — in bundled mode, writable state goes to `~/Library/Application Support/Local LLM/`. Sets `LOCAL_LLM_DATA_DIR` env var. |
| `stop.sh` | Graceful shutdown via PID file. Detects bundled data directory same way as start.sh. |
| `restart.sh` | Stop + start. |
| `uninstall.sh` | Stop server, optionally remove entire HF cache (`~/.cache/huggingface`), remove project directory. |
| `make_app.sh` | Builds a self-contained `Local LLM.app` + `Local LLM.dmg`. The project is bundled inside `Resources/project/` and the Swift wrapper uses `Bundle.main.resourcePath` at runtime (no path injection). |
| `database/chats.db` | SQLite database with tables: `chats`, `messages`, `models`, `documents`, `settings`. In bundled mode, stored in the data directory. |

---

## API Routes

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/api/chats?q=` | List all chats (id, title, updated_at). Optional `q` parameter searches by title. |
| `GET` | `/api/chats/{chat_id}/messages` | Get messages for a chat |
| `POST` | `/api/chats/{chat_id}/messages/truncate` | Truncate conversation from a specific index (deletes DB messages + summary sync + file asset cleanup) |
| `POST` | `/api/chat?chat_id=` | Send message (includes `system_prompt` for new chats), returns SSE tokens |
| `GET` | `/api/chats/{chat_id}/system-prompt` | Get the system prompt for a chat |
| `PUT` | `/api/chats/{chat_id}/system-prompt` | Update the system prompt for a chat |
| `POST` | `/api/chats/{chat_id}/generate-title`| Auto-generates or refines a chat title using a tiered context strategy |
| `DELETE` | `/api/chats/{chat_id}` | Delete a chat (including all messages, docs, and physical assets) |
| `GET` | `/api/chats/{chat_id}/rag-status` | Get RAG pagination offset and total chunks |
| `PUT` | `/api/chats/{chat_id}/rag-status` | Update persistent RAG pagination offset |
| `POST` | `/api/upload-document` | Upload file for RAG (multipart form) |
| `GET` | `/api/models` | List models in library |
| `POST` | `/api/models` | Add model (verify + download), returns SSE progress |
| `POST` | `/api/models/active` | Switch active model, returns SSE progress |
| `DELETE` | `/api/models/{name}` | Delete model from DB + disk cache |
| `GET` | `/api/config` | Get generation config |
| `PATCH` | `/api/config` | Update generation config |
| `POST` | `/api/say` | Speak text via macOS `say` command |
| `POST` | `/api/stop-say` | Terminate speech |
| `GET` | `/api/system-prompts?q=` | Search/list saved system prompt templates |
| `POST` | `/api/system-prompts` | Save a new system prompt template |
| `PUT` | `/api/system-prompts/{id}` | Update an existing template |
| `DELETE` | `/api/system-prompts/{id}` | Delete a template |
| `GET` | `/api/hf-token/status` | Check if an HF token is stored in the Keychain |
| `POST` | `/api/hf-token/verify` | Verify a token without saving it |
| `POST` | `/api/hf-token/save` | Verify and save a token to the Keychain |
| `DELETE` | `/api/hf-token` | Remove the stored token |
| `GET` | `/api/hf-cache/info` | Get combined size of `~/.cache/huggingface` + app data dir |
| `DELETE` | `/api/hf-cache` | Delete both directories (Settings → App Data cleanup) |

---

## Key Concepts

### Model Management
- Models must `mlx` compatible.
- Models are stored in HF's cache at `~/.cache/huggingface/hub`.
- The server attempts VLM load first (`mlx_vlm`), falls back to standard LLM (`mlx_lm`).
- Only one model is loaded in GPU memory at a time. Switching unloads the previous model.
- The `state.IS_VLM` flag controls which generation path is used.

### Thinking Model Detection (first load)
- On first model switch, `sync_detect_thinking` sends a "hi" prompt and scans the response for 10 known thinking end-tag patterns via regex.
- Symmetric tags (same start/end, e.g. `<channel|>`): uses `rfind` to locate the **last** occurrence (the actual end). Closing tags (e.g. `</think>`): uses `find` for the first/only occurrence.
- Result persisted to `models.has_thinking` (NULL→0/1) and `models.thinking_end_tag`. Only updates when `has_thinking IS NULL` — never re-checks.
- Default fallback model (`gemma-4-e2b-it-4bit`) is skipped. VLM/LM type is also persisted during initialization.
- Frontend model select exposes `data-has-thinking` and `data-thinking-end-tag` on each option for the typing indicator.

### Streaming Responses
- All generation endpoints use **Server-Sent Events (SSE)**.
- The SSE protocol uses `data: {json}\n\n` lines with a final `data: [DONE]\n\n`.
- The frontend parses SSE chunks and renders markdown incrementally with throttled re-renders (~20fps).
- **Thinking models**: Worker buffers tokens until the end tag is found, then emits `thinking_start` → `thinking` raw tokens → `thinking_done` before regular `content` tokens. Frontend renders a collapsible "Thought" section with brain icon and pulse animation. Thinking content is stored separately from the clean response in the `messages` table.

### Message Edit & Regenerate
- **Edit button** (pencil icon): visible on every user message when hovering. Clicking replaces the message with a styled textarea (accent border, auto-resize to 300px max, Escape to cancel).
  - On "Save & Submit": calls `POST /api/chats/{chat_id}/messages/truncate` with the message's index, removes that message and everything after from the DOM, then calls `sendMessage(newContent, forceTitleRegen=true)`.
- **Regenerate button** (refresh icon): visible on every assistant message when hovering. Walks backward to find the preceding user message, truncates from that point, and re-sends the original user content.
  - `sendMessage(content, forceTitleRegen=true)` forces title refinement even if it's not the 3rd turn.
- **Truncate endpoint** (server/routes/chat.py:428): Deletes DB messages from index onward, resets `summary_through_msg_id` watermark, and cleans up orphaned image/upload files on disk by regex-scanning truncated message content.
- **Keyboard shortcut**: ArrowUp with empty input autofocuses the last user message's edit button.

### Keyboard Shortcuts
Enabled via `app.js::initKeyboardShortcuts()`:
| Shortcut | Action |
|----------|--------|
| `Ctrl/Cmd+Shift+N` | New chat |
| `Ctrl/Cmd+/` | Focus input |
| `Escape` | Abort in-flight generation |
| `ArrowUp` (empty input) | Edit last user message |

### Drag & Drop File Upload
Enabled via `app.js::initDragAndDrop()`. Files dragged over the chat window show a full-screen overlay (darkened background, blur, dashed accent border with file-up icon). On drop, files are forwarded to the existing file upload input, triggering the normal upload flow. Uses a drag counter to handle nested enter/leave events correctly.

### Special Commands (Prefix-Based Routing)
- `/web <query>` — Scrapes DuckDuckGo and injects results as context before the LLM prompt.
- `/imagine <prompt>` — Bypasses LLM, boots FLUX.1 Schnell for text-to-image generation.
- `/edit <prompt>` — Image-to-image editing with FLUX using the most recently uploaded image.
- `/next` — Advances the RAG pagination window to show the next batch (syncs with UI slider).

### RAG (Retrieval-Augmented Generation)
- Documents are chunked on upload (800 chars for text, full file for code).
- Embeddings computed via `sentence-transformers/all-MiniLM-L6-v2`.
- Persisted to SQLite `documents` table (embeddings as numpy BLOBs) and lazy-loaded into `state.document_store` memory on first chat access.
- At query time, chunks are injected in sequential page order (Default) or filtered by similarity to a user-defined topic (Search Mode).
- Pagination via `state.rag_offsets` dict and persisted `chats.rag_offset` DB column.
- UI Control: Interactive slider in the chat header for manual scrubbing, plus a Search Toggle (🔍) to filter the document by topic.
- Persistence: Reading position, search mode, and search topic are saved to the `chats` table and restored on load.
- Code files (detected by extension) are included in full rather than chunked.

### Dual-Layer Memory System
The chat endpoint uses a two-layer memory system instead of sending the entire conversation history:
- **Rolling Window** — Token-budget-aware: fills recent messages newest-first until the context budget is spent. Provides short-term conversational coherence.
- **Progressive Summary** — Older messages that fall out of the rolling window are incrementally summarized by the LLM and stored on the `chats.summary` column. Runs asynchronously post-generation.
- **Context Assembly** (`assemble_context()`) allocates the model's context window as: system prompt → summary → rolling window → current message, with generation headroom reserved.
- Configurable via `config.json`: `rolling_window_max_tokens`, `summary_max_tokens`, `context_window_pct` (1-100% of model's full context — lower = less RAM).

### Dynamic Title Refinement
The chat title is auto-refined after the first turn and every 3 turns by the frontend, using a hybrid generation strategy:

**Title generation flow** (short-circuits at first match):

1. **Programmatic titles** — First user message checked for known patterns, LLM skipped entirely:
   - `[Attached Document/Scanned Document: name]` → `Doc: name`
   - `[Attached Image: name]` → `Image: name`
   - `/imagine prompt` → `Generated: prompt`
   - `/edit prompt` → `Edited Image`

2. **Short prompt bypass** — If `title_is_fallback` and text ≤ 5 words, use raw text directly.

3. **Main model path** (non-thinking models only) — Uses the already-loaded worker model via `state.model_manager.sync_nonstream_generate()` with the `generation_lock` (non-blocking). Thinking models skip this path to avoid waiting on chain-of-thought. Falls through on lock-busy or failure.

4. **Title worker fallback** — One-shot subprocess (`title_worker.py`) using `mlx-community/Llama-3.2-1B-Instruct-4bit`. Never blocks the main model.

- **Context Strategy** (tiered by conversation state):
  - **No summary + ≥6 user words**: All messages (user + assistant) with `User:`/`Assistant:` labels. Assistant messages truncated at 300 words.
  - **Summary exists**: Summary text + latest 4 messages as a sanity check.
  - **Latest user message < 7 chars**: Uses just that message (handles single-word queries).
  - **Otherwise**: Last 10 messages with role labels.
- **Protocol**: Main model path uses `sync_nonstream_generate`; title worker uses stdin JSON `{"prompt": "..."}` → stdout JSON `{"title": "..."}`.
- **Prompt rules**: No think tags, no emojis, no special symbols, plain text only.

### Image Generation
- Uses `mflux` library (FLUX.1 Schnell, 4-bit quantized).
- **VRAM sharing:** The LLM is fully unloaded before FLUX runs, then reloaded after.
- Both `/imagine` and `/edit` use the shared `run_flux_pipeline()` service.
- Progress is reported via SSE with ASCII progress bars.
- Generated images saved to `static/images/` and served as markdown `![](...)`.

### Asset Management & Cleanup
- **SQLite Foreign Keys:** Enabled globally via `PRAGMA foreign_keys = ON`.
- **Chat Deletion:** Deleting a chat automatically triggers a filesystem cleanup in `static/uploads/` and `static/images/`.
- **Documents:** PDF sources, extracted page images, and uploaded files referenced in the `documents` table are deleted.
- **Images:** Generated images referenced in `messages` are parsed and deleted from `static/images/`.

---

## Global State (server/state.py)

These module-level variables are the core runtime state. All modules import `server.state` and read/write these directly:

```python
MODEL_NAME = None          # Current model's HF repo ID (e.g. "mlx-community/gemma-4-e2b-it-4bit")
model_manager = None       # ModelManager instance (manages child worker process)
generation_lock = threading.Lock() # Protects model access across threads

document_store = {}        # chat_id -> [{type, text, emb, ...}]  (lazy-loaded from SQLite)
rag_offsets = {}           # chat_id -> int offset for pagination
embedder_model = None      # SentenceTransformer instance (lazy loaded)
say_processes = set()      # Tracked subprocess.Popen objects for TTS
```

The actual MLX model objects (`model`, `tokenizer`, `processor`, `vlm_config`, `IS_VLM`) live in the child worker process (`server/services/worker.py`). The parent accesses them indirectly through `state.model_manager` which proxies generation commands.

---

## Database Schema

```sql
-- Chat conversations
chats (id TEXT PK, title TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, system_prompt TEXT,
       summary TEXT, summary_through_msg_id INTEGER, rag_offset INTEGER,
       rag_search_mode BOOLEAN, rag_search_query TEXT, title_is_fallback BOOLEAN)  -- Memory summary + RAG + Title state

-- Messages within chats
messages (id INTEGER PK, chat_id TEXT FK, role TEXT, content TEXT, timestamp TIMESTAMP,
         embedding BLOB, generation_time_ms INTEGER, token_count INTEGER,
         thinking_content TEXT)
         -- Vector memory + generation stats for assistant messages
         -- thinking_content: raw thinking block with tags (null for non-thinking models)

-- Available models in the library
models (id INTEGER PK, name TEXT UNIQUE, active BOOLEAN, supports_vision BOOLEAN,
        supports_image_generation BOOLEAN, is_downloaded BOOLEAN, last_used TIMESTAMP,
        has_thinking INTEGER DEFAULT NULL, thinking_end_tag TEXT)
        -- has_thinking: NULL=unchecked, 0=non-thinking, 1=thinking
        -- thinking_end_tag: closing tag that separates thinking from response (e.g. </think>)

-- Document chunks for RAG
documents (id INTEGER PK, chat_id TEXT FK, file_name TEXT, content TEXT,
           embedding BLOB, type TEXT, metadata TEXT, created_at TIMESTAMP)

-- Key-value settings (schema exists but NOT currently used)
settings (key TEXT PK, value TEXT, updated_at TIMESTAMP)

-- Reusable system prompt templates
system_prompt_templates (id INTEGER PK AUTOINCREMENT, name TEXT, content TEXT,
                        created_at TIMESTAMP, updated_at TIMESTAMP)
```

---

## Frontend Patterns

### Libraries (bundled locally in `static/libs/`)
- **Lucide** — Icon library. Icons are declared as `<i data-lucide="icon-name">` and activated via `lucide.createIcons()`.
- **Marked.js** — Markdown parser. Custom renderer for code blocks with copy buttons.
- **DOMPurify** — HTML sanitizer. All rendered markdown passes through `DOMPurify.sanitize()`.
- **Prism.js** — Syntax highlighter applied dynamically to streaming code block rendering.
- **Google Fonts** — Inter and Outfit fonts are bundled directly in `static/fonts/` for strict offline support.

### ES Module Structure
The frontend uses ES modules (`type="module"` in the script tag). Key patterns:
- **state.js** exports a shared `state` object and `elements` map — modules import and mutate these directly.
- **app.js** is the entry point that imports all modules and wires event listeners.
- Functions needed by inline `onclick` handlers are attached to `window.*` in app.js: `sendMessage`, `stopGeneration`, `stopSpeaking`, `showToast`, `copyToClipboard`, `copyCode`, `editMessage`, `regenerateMessage`.

### State
- `state.currentChatId` — Active chat UUID (null = no chat selected).
- `state.abortController` — AbortController for cancelling in-flight generation requests.
- `state.isRecording` — Voice recording state.
- `state._userScrolledUp` — Prevents auto-scroll when user is reading mid-conversation.

### SSE Parsing
The frontend reads SSE streams token-by-token. Key data fields:
- `{chat_id}` — Received first, sets the active chat ID.
- `{content: "token"}` — Appended to the response buffer.
- `{replace: "full text"}` — Replaces entire response (used by image gen progress).
- `{rag_status: {offset, total, limit}}` — Updates the RAG pagination badge.
- `{model_badge: "text"}` — Temporarily changes the model name badge (during FLUX).
- `{model_badge_restore: true}` — Restores the badge to the active LLM name.
- `{error: "message"}` — Displays an error.
- `{model_crash: true, fallback_model: "...", fallback_model_display: "...", detail: "..."}` — Worker OOM crash: frontend shows persistent toast with filtered crash diagnostics, removes typing indicator.
- `{gen_stats: {tokens, time_ms, time_s, tps}}` — Generation stats emitted before `[DONE]`. Rendered in `.message-actions` as `N tokens · X.Xs · Y.Y t/s`.
- `{thinking_start: true}` — Thinking block begins. Frontend creates a collapsible `.message-thinking` section with brain icon.
- `{thinking: "text"}` — Raw thinking tokens (with tags). Displayed in the thinking body with tags stripped.
- `{thinking_done: true}` — Thinking block complete. Section auto-collapses, content rendered as markdown, response area revealed.

---

## Known Limitations & Tech Debt

1. **macOS only** — TTS uses `say` command; MLX requires Apple Silicon.

---

## Development Workflow

```bash
# Start the server (handles all setup automatically)
./start.sh

# Server runs at http://localhost:8000
# Logs: tail -f server.log

# Restart after code changes
./restart.sh

# Stop
./stop.sh

# Manual run (for development with live output)
./venv/bin/python3 server.py
```

### Key Dependencies
- `mlx_lm` / `mlx_vlm` — Apple MLX model loading and generation
- `mflux` — FLUX.1 image generation on Apple Silicon
- `sentence-transformers` — Embedding model for RAG
- `fastapi` + `uvicorn` — Web server
- `PyPDF2` + `pymupdf` (fitz) — PDF text extraction and page-to-image conversion
- `huggingface_hub` — Model discovery and download
- `keyring` — Secure token storage in macOS Keychain

### Environment Variables
- `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` — Set to `"1"` at startup in `server.py` to prevent network requests. Temporarily set to `"0"` during model downloads.
- `HF_TOKEN` — Set at startup from the Keychain (via `hf_auth.load_hf_token()`). Passed to the worker subprocess and used by FLUX image generation. Managed via Settings → 🔑 HuggingFace Token.
- `LOCAL_LLM_DATA_DIR` — Set by `start.sh` when running in bundled .app mode. Points to `~/Library/Application Support/Local LLM/` where writable state (config, database, venv, logs, pids) is stored. Python code checks this env var for paths that need to be writable; falls back to the project directory (file-relative or CWD) when unset.
