"""
Chat routes — CRUD for chats/messages and the main streaming generation endpoint.
"""

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
from server.models import ChatCreate
from server.services.rag import get_embedder, build_rag_context, handle_vision_pdf_pagination
from server.services.web_search import perform_web_search
from server.services.image_gen import run_flux_pipeline, flux_sse_generator

router = APIRouter()


@router.get("/api/chats")
def get_chats():
    # Sync route unblocks asyncio threadpool
    with closing(get_db_connection()) as conn:
        chats = conn.execute("SELECT * FROM chats ORDER BY created_at DESC").fetchall()
    return [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]} for c in chats]


@router.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: str):
    # Sync route unblocks asyncio threadpool
    with closing(get_db_connection()) as conn:
        messages = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp",
                                (chat_id,)).fetchall()
    return [{"role": m["role"], "content": m["content"]} for m in messages]


@router.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
    return {"status": "ok"}


@router.post("/api/chat")
async def chat_endpoint(chat_data: ChatCreate, chat_id: Optional[str] = None):
    # 1. Start or resume chat
    if not chat_id:
        chat_id = str(uuid.uuid4())

    with closing(get_db_connection()) as conn:
        chat_exists = conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat_exists:
            title = chat_data.message[:50] + "..." if len(chat_data.message) > 50 else chat_data.message
            conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, title))
            conn.commit()

    # 2. Save user message
    with closing(get_db_connection()) as conn:
        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                     (chat_id, "user", chat_data.message))
        conn.commit()

        # 3. Get history for prompt
        history = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp",
                               (chat_id,)).fetchall()

    # 4. Format prompt
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
    else:
        # Reset pagination for any new specific question to keep relevance high
        if chat_id in state.rag_offsets:
            print(f"New query detected, resetting RAG offset for {chat_id}.")
            state.rag_offsets[chat_id] = 0

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

    # --- Build message list with context injection ---
    messages = []

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

    for i, h in enumerate(history):
        content = h["content"]

        # Inject RAG / Web Context into the latest user message
        if i == len(history) - 1:
            doc_context = ""
            if chat_id in state.document_store and state.document_store[chat_id]:
                doc_context, text_rag_meta = build_rag_context(chat_id, content)
                if text_rag_meta and not rag_meta:
                    rag_meta = text_rag_meta

            combined_context = web_context + ("\n" if web_context and doc_context else "") + doc_context
            if combined_context:
                content = f"{combined_context}\nInstructions: Utilizing the context provided above, answer the following query:\n\n{content.replace('/web', '').strip()}"

        messages.append({"role": h["role"], "content": content})

    prompt = state.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    async def event_generator():
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
                    messages,  # Use the original messages list
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
            # 6. Save assistant message even if partially generated
            if full_response:
                with closing(get_db_connection()) as conn:
                    conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                                 (chat_id, "assistant", full_response))
                    conn.commit()

            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
