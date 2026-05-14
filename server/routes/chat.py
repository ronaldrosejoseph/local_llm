"""
Chat routes — CRUD for chats/messages and the main streaming generation endpoint.
"""

import os
import sys
import uuid
import json
import re
import asyncio
import queue
import threading

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from contextlib import closing
from typing import Optional

from server import state
from server.db import get_db_connection
from server.config import load_config
from server.models import ChatCreate, SystemPromptUpdate
from server.services.rag import get_embedder, build_rag_context, handle_vision_pdf_pagination, load_documents_from_db
from server.services.web_search import perform_web_search
from server.services.image_gen import run_flux_pipeline, flux_sse_generator
from server.services.memory import assemble_context, post_generation_tasks
from server.services.model_manager import InferenceCrash

router = APIRouter()


def _check_hf_token() -> bool:
    """Return True if an HF token is stored in the keyring."""
    try:
        from server.services.hf_auth import has_token
        return has_token()
    except Exception:
        return False


@router.get("/api/chats")
def get_chats(q: Optional[str] = None):
    with closing(get_db_connection()) as conn:
        if q and q.strip():
            search_term = f"%{q.strip()}%"
            chats = conn.execute("SELECT * FROM chats WHERE title LIKE ? ORDER BY updated_at DESC", (search_term,)).fetchall()
        else:
            chats = conn.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
    return [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"], "is_fallback": bool(c["title_is_fallback"])} for c in chats]


@router.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: str):
    with closing(get_db_connection()) as conn:
        messages = conn.execute(
            "SELECT role, content, generation_time_ms, token_count "
            "FROM messages WHERE chat_id = ? ORDER BY timestamp",
            (chat_id,),
        ).fetchall()
    return [
        {
            "role": m["role"],
            "content": m["content"],
            "generation_time_ms": m["generation_time_ms"] or 0,
            "token_count": m["token_count"] or 0,
        }
        for m in messages
    ]


@router.post("/api/chats/{chat_id}/generate-title")
async def generate_title_route(chat_id: str):
    """API endpoint to manually trigger title generation."""
    return await internal_generate_title(chat_id)


async def internal_generate_title(chat_id: str):
    """
    Internal helper to summarize chat context into a title.
    Can be called by API routes or background memory tasks.
    """
    import re
    # 1. Fetch current chat info
    with closing(get_db_connection()) as conn:
        # Priority: 1. Existing Summary, 2. Recent context (last 3 turns), 3. First message
        chat = conn.execute("SELECT title, title_is_fallback, summary FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat:
            return {"error": "Chat not found"}
            
        source_text = ""
        if chat["summary"]:
            source_text = chat["summary"]
            print(f"Title Gen: Using Summary as source for {chat_id}")
        else:
            # Get last 3 turns of context for a more relevant "Recent" title
            recent_msgs = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 6",
                (chat_id,)
            ).fetchall()
            
            if len(recent_msgs) > 2:
                # Join recent turns (reversed because we fetched DESC)
                source_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in reversed(recent_msgs)])
                print(f"Title Gen: Using Recent Context (last {len(recent_msgs)//2} turns) for {chat_id}")
            else:
                # Fallback to the very first user message
                first_msg_row = conn.execute(
                    "SELECT content FROM messages WHERE chat_id = ? AND role = 'user' ORDER BY timestamp ASC LIMIT 1",
                    (chat_id,)
                ).fetchone()
                source_text = first_msg_row["content"] if first_msg_row else ""
                print(f"Title Gen: Using First Message as source for {chat_id}")
        
        first_message = source_text

    # 2. Clean up the prompt (strip commands and attachments)
    clean_text = first_message.strip()
    clean_text = re.sub(r'^/(imagine|edit|web|next)\s*', '', clean_text).strip()
    clean_text = re.sub(r'^\[Attached (Document|Image|Scanned Document): (.*?)\]', r'\2', clean_text).strip()
    clean_text = clean_text.strip("[]\"' ")

    # 3. Handle model missing (Fallback path)
    if state.model_manager is None or state.model_manager.model_name is None:
        if chat["title"] == "New Conversation" or chat["title_is_fallback"]:
            words = clean_text.split()
            fallback_title = " ".join(words[:5])
            if len(words) > 5: fallback_title += "..."
            
            with closing(get_db_connection()) as conn:
                conn.execute("UPDATE chats SET title = ?, title_is_fallback = 1 WHERE id = ?", (fallback_title, chat_id))
                conn.commit()
            return {"title": fallback_title, "status": "fallback"}
        return {"title": chat["title"], "status": "still_fallback"}

    # 4. Model is loaded - Proceed with LLM-based generation
    if not state.generation_lock.acquire(blocking=True, timeout=5.0):
        return {"error": "Model busy"}
        
    try:
        prompt_txt = (
            "Summarize the text strictly into a title of exactly 2–5 words.\n"
            "Output plain text only.\n"
            "Do NOT use:\n"
            "- asterisks\n"
            "- markdown\n"
            "- quotes\n"
            "- emojis\n"
            "- brackets\n"
            "- colons\n"
            "- punctuation of any kind\n"
            "- prefixes like 'Title' or 'Summary'\n"
            "Return ONLY the 2–5 word title with no extra text.\n\n"
            f"{clean_text}"
        )
        messages = [{"role": "user", "content": prompt_txt}]

        result = await state.model_manager.nonstream_generate(
            messages=messages,
            is_vlm=state.model_manager.is_vlm,
            max_tokens=12,
        )

        title = result.strip().strip('"').strip("'") if result else ""
        
        with closing(get_db_connection()) as conn:
            conn.execute("UPDATE chats SET title = ?, title_is_fallback = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (title, chat_id))
            conn.commit()
            
        return {"title": title}
    except Exception as e:
        print(f"Failed to generate title: {e}")
        return {"error": str(e)}
    finally:
        state.generation_lock.release()

@router.get("/api/chats/{chat_id}/rag-status")
def get_rag_status(chat_id: str):
    from server.services.rag import build_rag_context
    # Fetch status directly from build_rag_context to ensure 'total' reflects the current mode/filter
    res = build_rag_context(chat_id, "")
    
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT rag_search_mode, rag_search_query FROM chats WHERE id = ?", (chat_id,)).fetchone()
        search_mode = bool(row["rag_search_mode"]) if row else False
        search_query = row["rag_search_query"] if row else ""
        
    if res:
        doc_context, rag_meta = res
        if rag_meta:
            rag_meta["search_mode"] = search_mode
            rag_meta["search_query"] = search_query
            return rag_meta
    
    limit = load_config().get("pdf_text_pages_per_batch", 50)
    return {"offset": 0, "total": 0, "limit": limit, "search_mode": search_mode, "search_query": search_query}

@router.put("/api/chats/{chat_id}/rag-status")
def update_rag_status(chat_id: str, payload: dict):
    with closing(get_db_connection()) as conn:
        try:
            if "offset" in payload:
                conn.execute("UPDATE chats SET rag_offset = ? WHERE id = ?", (int(payload["offset"]), chat_id))
                state.rag_offsets[chat_id] = int(payload["offset"])
            if "search_mode" in payload:
                conn.execute("UPDATE chats SET rag_search_mode = ? WHERE id = ?", (1 if payload["search_mode"] else 0, chat_id))
            if "search_query" in payload:
                conn.execute("UPDATE chats SET rag_search_query = ? WHERE id = ?", (payload["search_query"], chat_id))
            conn.commit()
        except Exception as e:
            print(f"Could not update rag_status in DB: {e}")
    return {"status": "success"}

@router.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    # 1. Gather all file paths linked to this chat before deleting records (due to CASCADE)
    files_to_delete = []
    
    # Check for the chat-specific PDF (scanned PDF processing)
    pdf_path = f"static/uploads/{chat_id}.pdf"
    if os.path.exists(pdf_path):
        files_to_delete.append(pdf_path)

    with closing(get_db_connection()) as conn:
        # Get paths from documents table (RAG attachments/Vision images)
        doc_rows = conn.execute("SELECT metadata FROM documents WHERE chat_id = ?", (chat_id,)).fetchall()
        for row in doc_rows:
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                    if "path" in meta:
                        files_to_delete.append(meta["path"])
                except:
                    pass
        
        # Get paths from messages table (Generated images via /imagine or /edit)
        msg_rows = conn.execute("SELECT content FROM messages WHERE chat_id = ?", (chat_id,)).fetchall()
        for row in msg_rows:
            content = row["content"]
            # Look for markdown image patterns: ![/images/filename.png] or (/images/filename.png)
            matches = re.findall(r'\(/(images|uploads)/([^)]+)\)', content)
            for folder, filename in matches:
                files_to_delete.append(os.path.join("static", folder, filename))

    # 2. Delete the chat record (Cascades to documents and messages tables)
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()

    # 3. Clean up the physical files
    for path in set(files_to_delete):
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"Deleted chat asset: {path}")
            except Exception as e:
                print(f"Failed to delete chat asset {path}: {e}")

    # 4. Clean up in-memory RAG state
    if chat_id in state.document_store:
        del state.document_store[chat_id]
    if chat_id in state.rag_offsets:
        del state.rag_offsets[chat_id]

    return {"status": "ok"}


# --- System Prompt Endpoints ---

@router.get("/api/chats/{chat_id}/system-prompt")
def get_system_prompt(chat_id: str):
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT system_prompt FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return {"system_prompt": (row["system_prompt"] or "") if row else ""}


@router.put("/api/chats/{chat_id}/system-prompt")
def set_system_prompt(chat_id: str, data: SystemPromptUpdate):
    with closing(get_db_connection()) as conn:
        conn.execute("UPDATE chats SET system_prompt = ? WHERE id = ?", (data.system_prompt, chat_id))
        conn.commit()
    return {"status": "ok"}


# --- Main Chat Endpoint ---

@router.post("/api/chat")
async def chat_endpoint(chat_data: ChatCreate, chat_id: Optional[str] = None):
    # 1. Start or resume chat
    if not chat_id:
        chat_id = str(uuid.uuid4())

    with closing(get_db_connection()) as conn:
        chat_exists = conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat_exists:
            title = chat_data.message[:50] + "..." if len(chat_data.message) > 50 else chat_data.message
            conn.execute("INSERT INTO chats (id, title, system_prompt, title_is_fallback) VALUES (?, ?, ?, ?)", 
                         (chat_id, title, chat_data.system_prompt, 1))
            conn.commit()

    # 2. Save user message
    with closing(get_db_connection()) as conn:
        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                     (chat_id, "user", chat_data.message))
        conn.commit()

    # 3. Load system prompt
    system_prompt = ""
    with closing(get_db_connection()) as conn:
        sp_row = conn.execute("SELECT system_prompt FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if sp_row and sp_row["system_prompt"]:
            system_prompt = sp_row["system_prompt"]

    # 5. Format prompt
    message_content = chat_data.message

    # --- RAG Pagination Detection ---
    is_next_command = False
    if re.search(r'(\bnext\b|\bmore\b)\s+(\b\d+\b|\bcontext\b|\bpage\b|\bchunks\b)',
                 message_content.lower()) or message_content.strip().startswith('/next'):
        is_next_command = True

        cfg_d = load_config()
        increment = cfg_d.get("pdf_text_pages_per_batch", 50)
        if chat_id in state.document_store:
            for item in state.document_store[chat_id]:
                if item.get("type") == "pdf_metadata":
                    increment = cfg_d.get("pdf_image_pages_per_batch", 5)
                    break

        state.rag_offsets[chat_id] = state.rag_offsets.get(chat_id, 0) + increment
        print(f"Pagination triggered for {chat_id}: New offset = {state.rag_offsets[chat_id]}")

    # --- Image Generation: /imagine ---
    if message_content.strip().startswith("/imagine"):
        if not _check_hf_token():
            async def no_token_gen():
                msg = (
                    "**⚠️ HuggingFace Token Required**\n\n"
                    "To use `/imagine`, add your HuggingFace token in "
                    "**Settings → 🔑 HuggingFace Token**. "
                    "It's stored securely in your macOS Keychain."
                )
                yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
                yield f"data: {json.dumps({'content': msg})}\n\n"
                yield 'data: [DONE]\n\n'
            return StreamingResponse(no_token_gen(), media_type="text/event-stream")
        prompt = message_content.strip()[8:].strip()
        print(f"Triggering image generation for: {prompt}")

        q = queue.Queue()
        img_name = f"gen_{uuid.uuid4().hex[:8]}.png"

        threading.Thread(target=run_flux_pipeline, args=(
            prompt, chat_id, q, img_name
        ), kwargs=dict(steps=4, result_message="Here is the image you requested:")).start()

        return StreamingResponse(
            flux_sse_generator(chat_id, q,
                               title="Diffusers Pipeline Active",
                               action_text="Generating image natively",
                               progress_text="Processing tensors",
                               alt_text="Generated Image"),
            media_type="text/event-stream"
        )

    # --- Image Editing: /edit ---
    if message_content.strip().startswith("/edit"):
        if not _check_hf_token():
            async def no_token_gen():
                msg = (
                    "**⚠️ HuggingFace Token Required**\n\n"
                    "To use `/edit`, add your HuggingFace token in "
                    "**Settings → 🔑 HuggingFace Token**. "
                    "It's stored securely in your macOS Keychain."
                )
                yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
                yield f"data: {json.dumps({'content': msg})}\n\n"
                yield 'data: [DONE]\n\n'
            return StreamingResponse(no_token_gen(), media_type="text/event-stream")
        prompt = message_content.strip()[5:].strip()
        print(f"Triggering image editing for: {prompt}")

        # Verify an image has been uploaded to this session
        source_image_path = None
        if chat_id in state.document_store:
            for doc in reversed(state.document_store[chat_id]):
                if doc.get("type") == "image":
                    source_image_path = doc["path"]
                    break

        if not source_image_path:
            async def error_gen():
                yield f'data: {json.dumps({"chat_id": chat_id})}\n\n'
                yield f'data: {json.dumps({"content": "**Error:** You must attach an image using the paperclip icon before using `/edit`."})}\n\n'
                yield 'data: [DONE]\n\n'
            return StreamingResponse(error_gen(), media_type="text/event-stream")

        q = queue.Queue()
        img_name = f"edit_{uuid.uuid4().hex[:8]}.png"

        threading.Thread(target=run_flux_pipeline, args=(
            prompt, chat_id, q, img_name
        ), kwargs=dict(
            source_image_path=source_image_path,
            strength=0.15, steps=8,
            result_message="Here is your edited image:"
        )).start()

        return StreamingResponse(
            flux_sse_generator(chat_id, q,
                               title="Diffusers Img2Img Active",
                               action_text="Editing image natively",
                               progress_text="Transforming matrices",
                               alt_text="Edited Image"),
            media_type="text/event-stream"
        )

    # --- Build context using hybrid memory system ---
    web_context = ""
    if message_content.strip().startswith("/web"):
        query = message_content.strip()[4:].strip()
        print(f"Triggering web search for: {query}")
        web_context = perform_web_search(query)

    # Hoist RAG status variables for event_generator
    rag_meta = None

    # --- Vision PDF Pagination & Extraction (On-demand) ---
    vision_rag_meta = handle_vision_pdf_pagination(chat_id)
    if vision_rag_meta:
        rag_meta = vision_rag_meta

    # Build RAG document context (existing system)
    doc_context = ""
    if chat_id in state.document_store and state.document_store[chat_id]:
        doc_context, text_rag_meta = build_rag_context(chat_id, message_content)
        if text_rag_meta and not rag_meta:
            rag_meta = text_rag_meta

    # Assemble context via hybrid memory pipeline
    messages = assemble_context(
        chat_id=chat_id,
        current_message=message_content,
        system_prompt=system_prompt,
        rag_context=doc_context,
        web_context=web_context,
    )

    async def event_generator():
        # --- Concurrency: acquire generation lock ---
        if not state.generation_lock.acquire(blocking=False):
            yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
            yield f"data: {json.dumps({'error': 'Model is currently busy with another request. Please wait and try again.'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Yield metadata first: chat_id and RAG window info
        yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
        if rag_meta:
            yield f"data: {json.dumps({'rag_status': rag_meta})}\n\n"

        full_response = ""
        import time
        token_count = 0
        gen_start = None  # set on first token (excludes prefill/thinking time)
        try:
            cfg = load_config()

            # Collect VLM image paths if applicable
            image_paths = None
            if state.model_manager and state.model_manager.is_vlm:
                if chat_id in state.document_store:
                    all_images = [d["path"] for d in state.document_store[chat_id] if d.get("type") == "image"]
                    if all_images:
                        offset = state.rag_offsets.get(chat_id, 0)
                        limit = cfg.get("pdf_image_pages_per_batch", 5)
                        if offset >= len(all_images):
                            offset = 0
                        image_paths = all_images[offset: offset + limit]

            async for token in state.model_manager.stream_generate(
                messages=messages,
                is_vlm=state.model_manager.is_vlm,
                image_paths=image_paths,
                max_tokens=cfg["max_tokens"],
                temperature=cfg["temperature"],
                top_p=cfg["top_p"],
                repetition_penalty=cfg["repetition_penalty"],
            ):
                if gen_start is None:
                    gen_start = time.monotonic()
                token_count += 1
                full_response += token
                yield f"data: {json.dumps({'content': token})}\n\n"
                await asyncio.sleep(0)

        except InferenceCrash as e:
            print(f"Chat generation for {chat_id}: model worker crashed", file=sys.stderr)
            fallback_display = state.DEFAULT_MODEL.split("/")[-1]
            crash_detail = str(e) if str(e) != "Worker process error" else ""
            yield f"data: {json.dumps({'model_crash': True, 'fallback_model': state.DEFAULT_MODEL, 'fallback_model_display': fallback_display, 'detail': crash_detail})}\n\n"
            yield f"data: {json.dumps({'error': 'Model process crashed. Falling back to a smaller model. Please try again.'})}\n\n"

        except asyncio.CancelledError:
            print(f"Chat generation for {chat_id} was cancelled by client.")
        except Exception as e:
            print(f"Error during generation: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # Compute generation stats — prefer MLX-reported GPU timings
            gen_time_ms = 0
            tps = 0.0
            mlx_stats = getattr(state.model_manager, '_last_gen_stats', None)

            if mlx_stats and mlx_stats.get('tokens_per_second', 0) > 0:
                # MLX-reported stats (GPU-level timing, most accurate)
                token_count = mlx_stats.get('generation_tokens', token_count)
                tps = mlx_stats.get('tokens_per_second', 0)
                gen_time_ms = int((token_count / tps) * 1000) if tps > 0 else 0
            elif gen_start is not None and token_count > 0:
                # Manual timing fallback (first-token → last-token)
                gen_time_ms = int((time.monotonic() - gen_start) * 1000)
                elapsed_s = gen_time_ms / 1000
                tps = token_count / elapsed_s if elapsed_s > 0 else 0

            # Save assistant message with generation stats
            assistant_msg_id = None
            if full_response:
                with closing(get_db_connection()) as conn:
                    cursor = conn.execute(
                        "INSERT INTO messages (chat_id, role, content, generation_time_ms, token_count) VALUES (?, ?, ?, ?, ?)",
                        (chat_id, "assistant", full_response, gen_time_ms, token_count),
                    )
                    assistant_msg_id = cursor.lastrowid
                    conn.commit()

            # Yield stats to frontend before [DONE]
            if token_count > 0:
                elapsed_s = gen_time_ms / 1000 if gen_time_ms > 0 else 0
                yield f"data: {json.dumps({'gen_stats': {'tokens': token_count, 'time_ms': gen_time_ms, 'time_s': round(elapsed_s, 1), 'tps': round(tps, 1)}})}\n\n"

            # Release concurrency lock
            state.generation_lock.release()

            # Kick off async memory tasks (embedding + summarization)
            if full_response and assistant_msg_id:
                post_generation_tasks(chat_id, chat_data.message, full_response, assistant_msg_id)

            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

