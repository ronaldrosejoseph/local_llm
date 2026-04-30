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
| `server/app.py` | FastAPI app creation, router includes, static file mount, startup model load. |
| `server/state.py` | All global mutable state: model, tokenizer, processor, document_store, etc. |
| `server/config.py` | Config load/save from `config.json` with defaults. |
| `server/db.py` | SQLite connection helper. |
| `server/models.py` | Pydantic request/response models (Message, ChatCreate, ConfigUpdate, etc.). |
| `server/services/llm.py` | Model loading (VLM-first, LLM fallback), VRAM cleanup, cache detection. |
| `server/services/rag.py` | Embedder loading, PDF-to-image, document chunking, semantic retrieval, vision PDF pagination. |
| `server/services/image_gen.py` | Shared FLUX pipeline for `/imagine` and `/edit` (deduplicated). |
| `server/services/memory.py` | Hybrid memory system: token-aware rolling window, progressive summarization, cross-chat vector retrieval. |
| `server/services/web_search.py` | DuckDuckGo scraping + weather widget. |
| `server/routes/chat.py` | Chat CRUD + main streaming generation endpoint. |
| `server/routes/model_routes.py` | Model list, add (SSE download), switch (SSE load), delete. |
| `server/routes/documents.py` | Document/image upload for RAG. |
| `server/routes/config_routes.py` | Generation config GET/PATCH. |
| `server/routes/speech.py` | TTS via macOS `say` command. |

### Frontend

| File | Purpose |
|------|---------|
| `static/index.html` | Single-page HTML shell. |
| `static/style.css` | Complete styling. Dark theme, responsive, glassmorphism. ~1300 lines. |
| `static/js/app.js` | **Entry point** — imports all modules, DOMContentLoaded init, event wiring, window globals. |
| `static/js/state.js` | Shared state object + DOM element references. |
| `static/js/utils.js` | Markdown rendering (Marked + DOMPurify), clipboard, scroll management. |
| `static/js/chat.js` | sendMessage with SSE parsing, appendMessage, typing indicator. |
| `static/js/sidebar.js` | Chat history, navigation, new/delete chat, modals, sidebar toggle. |
| `static/js/models.js` | Model loading, adding (SSE download), switching (SSE load). |
| `static/js/settings.js` | Settings modal, config sliders, model library UI. |
| `static/js/documents.js` | File upload handler, attachment pill UI. |
| `static/js/speech.js` | TTS (server API), speech-to-text (Web Speech API). |
| `static/js/system_prompt.js` | System prompt management — per-chat persona/instruction editing. |
| `static/uploads/` | Uploaded documents and images (runtime, gitignored). |
| `static/images/` | Generated images from FLUX (runtime, gitignored). |

### Other

| File | Purpose |
|------|---------|
| `init_db.py` | Database schema creation and migrations. Run once on first startup. |
| `config.json` | Runtime config (temperature, max_tokens, top_p, etc.). Read/written by server. |
| `requirements.txt` | Python dependencies. |
| `start.sh` | Bootstrap script: installs Homebrew/Python if needed, creates venv, installs deps, inits DB, launches server. |
| `stop.sh` | Graceful shutdown via PID file. |
| `restart.sh` | Stop + start. |
| `database/chats.db` | SQLite database with tables: `chats`, `messages`, `models`, `documents`, `settings`. |

---

## API Routes

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/api/chats?q=` | List all chats (id, title, updated_at). Optional `q` parameter searches by title. |
| `GET` | `/api/chats/{chat_id}/messages` | Get messages for a chat |
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

---

## Key Concepts

### Model Management
- Models must be from `mlx-community/` on Hugging Face.
- Models are stored in HF's cache at `~/.cache/huggingface/hub/`.
- The server attempts VLM load first (`mlx_vlm`), falls back to standard LLM (`mlx_lm`).
- Only one model is loaded in GPU memory at a time. Switching unloads the previous model.
- The `state.IS_VLM` flag controls which generation path is used.

### Streaming Responses
- All generation endpoints use **Server-Sent Events (SSE)**.
- The SSE protocol uses `data: {json}\n\n` lines with a final `data: [DONE]\n\n`.
- The frontend parses SSE chunks and renders markdown incrementally with throttled re-renders (~20fps).

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
- Configurable via `config.json`: `rolling_window_max_tokens`, `summary_max_tokens`.

### Dynamic Title Refinement
The chat title evolves as the conversation progresses to maintain relevance:
- **Tiered Context Strategy**:
  - **Turn 1**: Uses the first message (cleaned of commands).
  - **Turns 3-9**: Uses the last 3 message pairs (User + Assistant) to refine context.
  - **Turn 10+**: Uses the **Progressive Summary** as the definitive source for the title.
- **Triggering**: The frontend triggers a refinement on the first turn and every 3 turns thereafter.
- **Fallback Logic**: If the model is not loaded during the first turn, a temporary "fallback" title is generated from raw text, which is later "upgraded" once the model is active.

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
model = None               # Loaded MLX model object
tokenizer = None           # Loaded tokenizer
processor = None           # VLM processor (None for text-only models)
vlm_config = None          # Cached VLM config dict
IS_VLM = False             # True if current model is a Vision model
generation_lock = threading.Lock() # Protects model access across threads

document_store = {}        # chat_id -> [{type, text, emb, ...}]  (lazy-loaded from SQLite)
rag_offsets = {}           # chat_id -> int offset for pagination
embedder_model = None      # SentenceTransformer instance (lazy loaded)
say_processes = set()      # Tracked subprocess.Popen objects for TTS
```

---

## Database Schema

```sql
-- Chat conversations
chats (id TEXT PK, title TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, system_prompt TEXT,
       summary TEXT, summary_through_msg_id INTEGER, rag_offset INTEGER,
       rag_search_mode BOOLEAN, rag_search_query TEXT, title_is_fallback BOOLEAN)  -- Memory summary + RAG + Title state

-- Messages within chats
messages (id INTEGER PK, chat_id TEXT FK, role TEXT, content TEXT, timestamp TIMESTAMP,
         embedding BLOB)  -- Vector memory (turn-pair embeddings stored as numpy float32)

-- Available models in the library
models (id INTEGER PK, name TEXT UNIQUE, active BOOLEAN, supports_vision BOOLEAN,
        supports_image_generation BOOLEAN, is_downloaded BOOLEAN, last_used TIMESTAMP)

-- Document chunks for RAG
documents (id INTEGER PK, chat_id TEXT FK, file_name TEXT, content TEXT,
           embedding BLOB, type TEXT, metadata TEXT, created_at TIMESTAMP)

-- Key-value settings (schema exists but NOT currently used)
settings (key TEXT PK, value TEXT, updated_at TIMESTAMP)
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
- Functions needed by inline `onclick` handlers are attached to `window.*` in app.js.

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

### Environment Variables
- `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` — Set to `"1"` at startup in `server.py` to prevent network requests. Temporarily set to `"0"` during model downloads.
