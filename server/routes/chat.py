"""
Chat routes — CRUD for chats/messages and the main streaming generation endpoint.
"""

import os
import uuid
import json
import re
import asyncio
import queue
import threading

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from mlx_lm import stream_generate
from mlx_lm.sample_utils import make_sampler, make_repetition_penalty
from mlx_vlm.prompt_utils import apply_chat_template as apply_vlm_template
from mlx_vlm import stream_generate as stream_vlm_generate
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

router = APIRouter()


@router.get("/api/chats")
def get_chats(q: Optional[str] = None):
    with closing(get_db_connection()) as conn:
        if q and q.strip():
            search_term = f"%{q.strip()}%"
            chats = conn.execute("SELECT * FROM chats WHERE title LIKE ? ORDER BY updated_at DESC", (search_term,)).fetchall()
        else:
            chats = conn.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
    return [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"]} for c in chats]


@router.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: str):
    with closing(get_db_connection()) as conn:
        messages = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp",
                                (chat_id,)).fetchall()
    return [{"role": m["role"], "content": m["content"]} for m in messages]


@router.post("/api/chats/{chat_id}/generate-title")
def generate_title(chat_id: str):
    """
    Summarize the first message of a chat into a concise title and update the DB.
    """
    if not hasattr(state, "model") or state.model is None:
        return {"error": "Model not loaded"}

    with closing(get_db_connection()) as conn:
        msgs = conn.execute("SELECT content FROM messages WHERE chat_id = ? AND role = 'user' ORDER BY timestamp ASC LIMIT 1", (chat_id,)).fetchall()
        if not msgs:
            return {"error": "No user messages found"}
        first_message = msgs[0]["content"]

    # We must acquire lock securely since we invoke the standard LLM APIs
    if not state.generation_lock.acquire(blocking=True, timeout=5.0):
        return {"error": "Model busy"}
        
    try:
        prompt_txt = f"Summarize this prompt strictly into a title with exactly 2 to 5 words. Do not use quotes or punctuation at the end:\n\n{first_message}"
        messages = [{"role": "user", "content": prompt_txt}]
        
        if state.IS_VLM:
            from mlx_vlm import generate as generate_vlm
            from mlx_vlm.prompt_utils import apply_chat_template as apply_vlm_template
            
            prompt = apply_vlm_template(
                state.processor,
                state.vlm_config,
                messages,
                num_images=0
            )
            result = generate_vlm(
                state.model,
                state.processor,
                prompt=prompt,
                max_tokens=12,
                verbose=False
            )
        else:
            from mlx_lm import generate
            prompt = state.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            result = generate(
                state.model, 
                state.tokenizer, 
                prompt=prompt, 
                max_tokens=12, 
                verbose=False
            )
            
        text_result = result if isinstance(result, str) else getattr(result, "text", str(result))
        title = text_result.strip().strip('"').strip("'")
        
        with closing(get_db_connection()) as conn:
            conn.execute("UPDATE chats SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (title, chat_id))
            conn.commit()
            
        return {"title": title}
    except Exception as e:
        print(f"Failed to generate title: {e}")
        return {"error": str(e)}
    finally:
        state.generation_lock.release()

@router.get("/api/chats/{chat_id}/rag-status")
def get_rag_status(chat_id: str):
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT rag_offset FROM chats WHERE id = ?", (chat_id,)).fetchone()
        # Fallback to 0 if column is missing or value is null
        offset = 0
        if row and "rag_offset" in row.keys() and row["rag_offset"] is not None:
            offset = row["rag_offset"]
            
        total_chunks = conn.execute("SELECT COUNT(*) FROM documents WHERE chat_id = ? AND type = 'text'", (chat_id,)).fetchone()[0]
        
    state.rag_offsets[chat_id] = offset
    limit = load_config().get("pdf_text_pages_per_batch", 50)
    return {"offset": offset, "total": total_chunks, "limit": limit}

@router.put("/api/chats/{chat_id}/rag-status")
def update_rag_status(chat_id: str, payload: dict):
    offset = int(payload.get("offset", 0))
    with closing(get_db_connection()) as conn:
        try:
            conn.execute("UPDATE chats SET rag_offset = ? WHERE id = ?", (offset, chat_id))
            conn.commit()
        except Exception as e:
            print(f"Could not save rag_offset to DB: {e}")
    state.rag_offsets[chat_id] = offset
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
            conn.execute("INSERT INTO chats (id, title, system_prompt) VALUES (?, ?, ?)", 
                         (chat_id, title, chat_data.system_prompt))
            conn.commit()

    # 2. Save user message
    with closing(get_db_connection()) as conn:
        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                     (chat_id, "user", chat_data.message))
        conn.commit()

    # 3. Lazy-load RAG documents from DB if not already in memory
    if chat_id not in state.document_store:
        load_documents_from_db(chat_id)

    # 4. Load system prompt
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

    prompt = state.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

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
        try:
            cfg = load_config()

            if state.IS_VLM:
                # --- VLM Branch ---
                # Check for images in document_store
                image_paths = []
                if chat_id in state.document_store:
                    all_images = [d["path"] for d in state.document_store[chat_id] if d.get("type") == "image"]
                    # If we found images, we apply pagination windowing (5 images at a time)
                    if all_images:
                        offset = state.rag_offsets.get(chat_id, 0)
                        limit = cfg.get("pdf_image_pages_per_batch", 5)
                        if offset >= len(all_images):
                            offset = 0
                        image_paths = all_images[offset: offset + limit]
                        print(f"VLM: Sending {len(image_paths)} images (offset {offset}) to model.")

                # VLM prompt formatting needs the MESSAGES list, not the pre-templated string
                formatted_prompt = apply_vlm_template(
                    state.processor,
                    state.vlm_config,  # Use the cached config
                    messages,  # Use the assembled messages list
                    num_images=len(image_paths)
                )

                for response in stream_vlm_generate(
                    state.model,
                    state.processor,
                    prompt=formatted_prompt,
                    image=image_paths if image_paths else None,
                    max_tokens=cfg["max_tokens"],
                    temperature=cfg["temperature"],
                ):
                    full_response += response.text
                    yield f"data: {json.dumps({'content': response.text})}\n\n"
                    await asyncio.sleep(0)
            else:
                # --- standard LLM Branch ---
                sampler = make_sampler(temp=cfg["temperature"], top_p=cfg["top_p"])
                logits_processors = [
                    make_repetition_penalty(penalty=cfg["repetition_penalty"])
                ] if cfg["repetition_penalty"] > 1.0 else None
                for response in stream_generate(
                    state.model, state.tokenizer,
                    prompt=prompt,
                    max_tokens=cfg["max_tokens"],
                    sampler=sampler,
                    logits_processors=logits_processors,
                ):
                    full_response += response.text
                    yield f"data: {json.dumps({'content': response.text})}\n\n"
                    await asyncio.sleep(0)  # Yield control

        except asyncio.CancelledError:
            print(f"Chat generation for {chat_id} was cancelled by client.")
        except Exception as e:
            print(f"Error during generation: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # Save assistant message even if partially generated
            assistant_msg_id = None
            if full_response:
                with closing(get_db_connection()) as conn:
                    cursor = conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                                 (chat_id, "assistant", full_response))
                    assistant_msg_id = cursor.lastrowid
                    conn.commit()

            # Release concurrency lock
            state.generation_lock.release()

            # Kick off async memory tasks (embedding + summarization)
            if full_response and assistant_msg_id:
                post_generation_tasks(chat_id, chat_data.message, full_response, assistant_msg_id)

            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

