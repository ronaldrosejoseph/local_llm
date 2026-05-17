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
            "SELECT role, content, generation_time_ms, token_count, thinking_content "
            "FROM messages WHERE chat_id = ? ORDER BY timestamp",
            (chat_id,),
        ).fetchall()
    return [
        {
            "role": m["role"],
            "content": m["content"],
            "generation_time_ms": m["generation_time_ms"] or 0,
            "token_count": m["token_count"] or 0,
            "thinking_content": m["thinking_content"] or "",
        }
        for m in messages
    ]


@router.post("/api/chats/{chat_id}/generate-title")
async def generate_title_route(chat_id: str):
    """API endpoint to manually trigger title generation."""
    return await internal_generate_title(chat_id)


def _clean_title(title: str) -> str:
    """Post-process a generated title: strip parenthetical notes, limit word count."""
    title = re.sub(r'\s*\([^)]*\)\s*', ' ', title).strip()
    title = title.rstrip('.,;:!?)-"\'')
    words = title.split()
    if len(words) > 8:
        title = " ".join(words[:8])
    return title.strip()


async def internal_generate_title(chat_id: str):
    """
    Internal helper to summarize chat context into a title.
    Can be called by API routes or background memory tasks.
    """
    import re
    # 1. Fetch current chat info and build source text for the title prompt
    with closing(get_db_connection()) as conn:
        chat = conn.execute("SELECT title, title_is_fallback, summary FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat:
            return {"error": "Chat not found"}

        # Collect all user prompts for a lightweight full-context option
        all_user_msgs = conn.execute(
            "SELECT content FROM messages WHERE chat_id = ? AND role = 'user' ORDER BY timestamp ASC",
            (chat_id,)
        ).fetchall()
        if not all_user_msgs:
            return {"error": "No user messages available for title generation"}

        all_user_text = " | ".join([m["content"] for m in all_user_msgs])
        all_user_words = len(all_user_text.split())
        latest_user_content = (all_user_msgs[-1]["content"] or "").strip()

        # Programmatic titles for attachments and image commands — skip LLM.
        first_msg = (all_user_msgs[0]["content"] or "").strip()
        prog_title = None
        doc_match = re.match(r'^\[Attached (Document|Scanned Document): (.+?)\]', first_msg)
        if doc_match:
            prog_title = f"Doc: {doc_match.group(2).strip()[:60]}"
        img_match = re.match(r'^\[Attached Image: (.+?)\]', first_msg)
        if img_match:
            prog_title = f"Image: {img_match.group(1).strip()[:60]}"
        if first_msg.startswith("/imagine"):
            prompt = first_msg[8:].strip()[:50]
            prog_title = f"Generated: {prompt}" if prompt else "Generated Image"
        if first_msg.startswith("/edit"):
            prog_title = "Edited Image"
        if prog_title:
            conn.execute(
                "UPDATE chats SET title = ?, title_is_fallback = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (prog_title, chat_id),
            )
            conn.commit()
            print(f"Title Gen: Programmatic title '{prog_title}' for {chat_id}")
            return {"title": prog_title}

        source_text = ""
        if not chat["summary"] and all_user_words >= 6:
            # Short conversation — include assistant responses as well so the
            # title model has both sides of the exchange for better summaries.
            all_msgs = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp ASC",
                (chat_id,)
            ).fetchall()
            formatted_parts = []
            for m in all_msgs:
                content = (m["content"] or "").strip()
                if m["role"] == "assistant":
                    words = content.split()
                    if len(words) > 300:
                        content = " ".join(words[:300]) + "…"
                formatted_parts.append(f"{m['role'].capitalize()}: {content}")
            source_text = "\n".join(formatted_parts) if formatted_parts else all_user_text
            print(f"Title Gen: Using user+assistant context ({len(formatted_parts)} msgs) for {chat_id}")
        elif chat["summary"]:
            # Include latest messages alongside the summary as a sanity check
            recent_msgs = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 4",
                (chat_id,)
            ).fetchall()
            if len(recent_msgs) > 1:
                latest = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in reversed(recent_msgs)])
                source_text = f"{chat['summary']}\n\nLatest messages:\n{latest}"
                print(f"Title Gen: Summary + latest turns for {chat_id}")
            else:
                source_text = chat["summary"]
                print(f"Title Gen: Using Summary as source for {chat_id}")
        elif len(latest_user_content) < 7:
            source_text = latest_user_content
            print(f"Title Gen: Using latest user message for {chat_id}")
        else:
            # Get last 3-5 turns for a more relevant recent title
            recent_msgs = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 10",
                (chat_id,)
            ).fetchall()

            if len(recent_msgs) > 2:
                source_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in reversed(recent_msgs)])
                print(f"Title Gen: Using Recent Context (last {len(recent_msgs)//2} turns) for {chat_id}")
            else:
                source_text = all_user_text
                print(f"Title Gen: Using user message history for {chat_id}")

        first_message = source_text

        # Check if main model is available and non-thinking — thinking models
        # would waste tokens on chain-of-thought for a simple title prompt.
        can_use_main = False
        if state.model_manager is not None and state.MODEL_NAME:
            row = conn.execute(
                "SELECT has_thinking FROM models WHERE name = ?",
                (state.MODEL_NAME,),
            ).fetchone()
            if row is None or row["has_thinking"] != 1:
                can_use_main = True

    # 2. Clean up the prompt (strip commands, attachments, and thinking tags)
    clean_text = first_message.strip()
    clean_text = re.sub(r'^/(imagine|edit|web|next)\s*', '', clean_text).strip()
    clean_text = re.sub(r'^\[Attached (Document|Image|Scanned Document): (.*?)\]', r'\2', clean_text).strip()
    clean_text = re.sub(
        r'</?think>|</?thinking>|</?reasoning>|</?thought>|</?answer>|</?response>|'
        r'<channel\|>|<unused95>|<\|end\|>|◁/think▷',
        '', clean_text, flags=re.IGNORECASE,
    )
    clean_text = clean_text.strip("[]\"' ")

    # Strip common disclaimer/refusal boilerplate from start of assistant
    # responses — prevents titles like "Medical advice disclaimer" when the
    # LLM leads with "I am an AI and cannot provide medical advice..."
    clean_text = re.sub(
        r"(Assistant:\s*)I\b[^.]*?\b(?:cannot|cannot|can\'t)\b[^.]*?"
        r"\b(?:advice|diagnosis|opinion|consultation)\b[^.]*?\.\s*",
        r'\1', clean_text, flags=re.IGNORECASE,
    )
    clean_text = re.sub(
        r"(Assistant:\s*)As\b[^.]*?\b(?:cannot|cannot|can\'t)\b[^.]*?"
        r"\b(?:advice|diagnosis|opinion|consultation)\b[^.]*?\.\s*",
        r'\1', clean_text, flags=re.IGNORECASE,
    )

    # 3. On first title generation, short prompts don't need an LLM.
    #    For title updates use the LLM — the conversation may have evolved.
    if chat["title_is_fallback"]:
        word_count = len(clean_text.split())
        if word_count <= 5:
            title = clean_text[:80]
            with closing(get_db_connection()) as conn:
                conn.execute(
                    "UPDATE chats SET title = ?, title_is_fallback = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (title, chat_id),
                )
                conn.commit()
            return {"title": title, "status": "short_prompt"}

    # 4. Try main model first (non-thinking models only, to avoid waiting on
    #    chain-of-thought). Falls back to title_worker if busy or thinking.
    if can_use_main:
        prompt_main = (
            "Do NOT use: emojis, symbols, asterisks, **, quotes, brackets, colons, punctuation\n"
            "Verify that the output is strictly a concise label with no extra text, no headings, no text formatting\n"
            "Here is a conversation. Summarize the main topic in 3-6 words."
            f"{clean_text}"
        )

        def _gen_with_main():
            if not state.generation_lock.acquire(blocking=False):
                return None
            try:
                result = state.model_manager.sync_nonstream_generate(
                    messages=[{"role": "user", "content": prompt_main}],
                    is_vlm=state.model_manager.is_vlm,
                    max_tokens=50,
                    timeout=15,
                )
                if not result:
                    return None
                result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
                result = re.sub(r'</?think>|</?thinking>', '', result, flags=re.IGNORECASE).strip()
                return result.strip('"\'').strip()
            except Exception as e:
                print(f"Title Gen (main model): {e}", file=sys.stderr)
                return None
            finally:
                state.generation_lock.release()

        title = await asyncio.to_thread(_gen_with_main)
        if title:
            title = _clean_title(title)
            with closing(get_db_connection()) as conn:
                conn.execute(
                    "UPDATE chats SET title = ?, title_is_fallback = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (title, chat_id),
                )
                conn.commit()
            print(f"Title Gen: Main model '{title}' for {chat_id}")
            return {"title": title}
        # Fall through to title_worker if main model was busy or failed

    # 5. Fall back to title_worker subprocess.
    #    Never blocks the main model — runs in its own MLX process.
    prompt_txt = (
        "You are a title generator. Your ONLY job is to read the text below "
        "which is a conversation between a user and a digital assistant "
        "and produce a short 3–6 word label that describes the TOPIC or SUBJECT.\n\n"
        "CRITICAL RULES:\n"
        "- Output a TOPIC LABEL, NOT an answer, opinion, moral, or response.\n"
        "- Do NOT include: think tags, reasoning tags, XML tags, or any markup.\n"
        "- Do NOT use: emojis, asterisks, **, quotes, brackets, colons, punctuation, "
        "or any special symbols.\n"
        "Now extract the topic from this text in a 3–6 words with no extra text, no headings, no formatting:\n\n"
        "Verify that the output is strictly a concise label with no extra text, no headings, no text formatting, and no Topic label:\n\n"
        f"{clean_text}"
    )

    try:
        worker_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services", "title_worker.py",
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, worker_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(
            json.dumps({"prompt": prompt_txt}).encode()
        )

        if proc.returncode != 0:
            stderr_text = stderr.decode() if stderr else ""
            print(f"Title worker failed (exit {proc.returncode}): {stderr_text[:500]}", file=sys.stderr)
            return {"error": "Title generation failed"}

        result = json.loads(stdout.decode())
        if "error" in result:
            print(f"Title worker error: {result['error']}", file=sys.stderr)
            return {"error": result["error"]}

        title = result.get("title", "").strip().strip('"').strip("'")
        if not title:
            return {"error": "Empty title"}
        title = _clean_title(title)

        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE chats SET title = ?, title_is_fallback = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, chat_id),
            )
            conn.commit()

        return {"title": title}

    except Exception as e:
        print(f"Failed to generate title: {e}", file=sys.stderr)
        return {"error": str(e)}

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
                    "It's stored securely in your macOS Keychain.\n\n"
                    "You also need to accept the terms for [FLUX.1-schnell]"
                    "(https://huggingface.co/black-forest-labs/FLUX.1-schnell) "
                    "by clicking **Agree to access repository** on its model card."
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
                    "It's stored securely in your macOS Keychain.\n\n"
                    "You also need to accept the terms for [FLUX.1-schnell]"
                    "(https://huggingface.co/black-forest-labs/FLUX.1-schnell) "
                    "by clicking **Agree to access repository** on its model card."
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
        thinking_content = ""  # raw thinking block including tags
        import time
        token_count = 0
        gen_start = None  # set on first regular token (excludes thinking time)

        # Check if current model is a known thinking model
        thinking_end_tag = None
        if state.MODEL_NAME:
            with closing(get_db_connection()) as conn:
                row = conn.execute(
                    "SELECT has_thinking, thinking_end_tag FROM models WHERE name = ?",
                    (state.MODEL_NAME,),
                ).fetchone()
            if row and row["has_thinking"] == 1:
                thinking_end_tag = row["thinking_end_tag"]

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

            async for ev_type, ev_text in state.model_manager.stream_generate(
                messages=messages,
                is_vlm=state.model_manager.is_vlm,
                image_paths=image_paths,
                max_tokens=cfg["max_tokens"],
                temperature=cfg["temperature"],
                top_p=cfg["top_p"],
                repetition_penalty=cfg["repetition_penalty"],
                thinking_end_tag=thinking_end_tag,
            ):
                if ev_type == "thinking_start":
                    yield f"data: {json.dumps({'thinking_start': True})}\n\n"
                    continue
                elif ev_type == "thinking":
                    thinking_content += ev_text
                    yield f"data: {json.dumps({'thinking': ev_text})}\n\n"
                    continue
                elif ev_type == "thinking_done":
                    yield f"data: {json.dumps({'thinking_done': True})}\n\n"
                    continue

                # Regular token
                if gen_start is None:
                    gen_start = time.monotonic()
                token_count += 1
                full_response += ev_text
                yield f"data: {json.dumps({'content': ev_text})}\n\n"
                await asyncio.sleep(0)

        except InferenceCrash as e:
            print(f"Chat generation for {chat_id}: model worker crashed", file=sys.stderr)
            fallback_display = state.DEFAULT_MODEL.split("/")[-1]
            crash_detail = str(e) if str(e) != "Worker process error" else ""
            yield f"data: {json.dumps({'model_crash': True, 'fallback_model': state.DEFAULT_MODEL, 'fallback_model_display': fallback_display, 'detail': crash_detail})}\n\n"
            yield f"data: {json.dumps({'error': 'Model process crashed. Falling back to a smaller model. Please try again.'})}\n\n"

        except asyncio.CancelledError:
            print(f"Chat generation for {chat_id} was cancelled by client.", file=sys.stderr)
            # Worker is still stuck in the MLX generation loop — kill it
            # and restart with the same model so the next request works.
            if state.model_manager:
                try:
                    await state.model_manager.cancel_generation()
                except Exception as cancel_err:
                    print(f"Failed to restart worker after cancel: {cancel_err}", file=sys.stderr)
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

            # Save assistant message with generation stats + thinking content
            assistant_msg_id = None
            if full_response:
                with closing(get_db_connection()) as conn:
                    cursor = conn.execute(
                        "INSERT INTO messages (chat_id, role, content, generation_time_ms, token_count, thinking_content) VALUES (?, ?, ?, ?, ?, ?)",
                        (chat_id, "assistant", full_response, gen_time_ms, token_count, thinking_content or None),
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

