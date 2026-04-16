"""
Document upload route — handles file uploads for RAG and Vision processing.
"""

import os
import re
import uuid
import io

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from contextlib import closing

from server import state
from server.db import get_db_connection
from server.config import load_config
from server.services.rag import get_embedder, pdf_to_images, CODE_EXTENSIONS

router = APIRouter()


@router.post("/api/upload-document")
async def upload_document(chat_id: str = Form(...), file: UploadFile = File(...)):
    try:
        content = await file.read()

        # Reject files that exceed the size limit before doing any processing
        if len(content) > state.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"File too large. Maximum upload size is {state.MAX_UPLOAD_BYTES // (1024 * 1024)}MB.")

        # Sanitize filename: strip path components and non-safe characters to prevent
        # path traversal attacks (e.g. filename='../../etc/passwd')
        raw_name = os.path.basename(file.filename or "upload")
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', raw_name) or "upload"

        # 1. Handle Vision Image Uploads
        if safe_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            os.makedirs("static/uploads", exist_ok=True)
            img_path = f"static/uploads/tmp_{uuid.uuid4().hex[:8]}_{safe_name}"
            with open(img_path, "wb") as f:
                f.write(content)

            if chat_id not in state.document_store:
                state.document_store[chat_id] = []
            # We flag this chunk as an image to bypass text RAG
            state.document_store[chat_id].append({"type": "image", "path": img_path})

            # Record attachment in DB so it shows up in history when switching chats
            with closing(get_db_connection()) as conn:
                # Ensure chat exists for this ID if this was the first action
                if not conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone():
                    conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)",
                                 (chat_id, f"Image: {file.filename}"))
                conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                             (chat_id, "user", f"[Attached Image: {file.filename}]"))
                conn.commit()

            return {"status": "ok", "chunks": 1, "filename": file.filename}

        # 2. Handle Text Docs and Code Files for RAG
        text = ""
        if safe_name.lower().endswith(".pdf"):
            from PyPDF2 import PdfReader

            # Hybrid Logic: Check if PDF has a digital text layer
            print(f"Analyzing PDF: {file.filename} for digital text...")
            pdf = PdfReader(io.BytesIO(content))
            digital_text = ""
            # Sampling first 3 pages for a quick "has-text" check
            for page in pdf.pages[:3]:
                t = page.extract_text()
                if t:
                    digital_text += t

            # THRESHOLD: If we found substantial text, prioritize RAG/Text path
            if len(digital_text.strip()) > 50:
                print("Digital text detected, using PyPDF2 for extraction.")
                text = ""
                for page in pdf.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
            else:
                # SCANNED/IMAGE PDF: Use Vision model if active
                if state.IS_VLM:
                    print("No digital text found (scanned): converting PDF to images via fitz...")

                    # Save PDF for on-demand extraction of further pages
                    os.makedirs("static/uploads", exist_ok=True)
                    pdf_path = f"static/uploads/{chat_id}.pdf"
                    with open(pdf_path, "wb") as f:
                        f.write(content)

                    limit_val = load_config().get("pdf_image_pages_per_batch", 5)
                    img_paths, total_pages = pdf_to_images(content, chat_id, start_page=0, limit=limit_val)
                    if chat_id not in state.document_store:
                        state.document_store[chat_id] = []

                    # Store PDF metadata for pagination reference
                    state.document_store[chat_id].append({
                        "type": "pdf_metadata",
                        "path": pdf_path,
                        "total_pages": total_pages,
                        "processed_pages": len(img_paths)
                    })

                    for p in img_paths:
                        state.document_store[chat_id].append({"type": "image", "path": p})

                    with closing(get_db_connection()) as conn:
                        if not conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone():
                            conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)",
                                         (chat_id, f"Doc: {file.filename}"))
                        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                                     (chat_id, "user", f"[Attached Scanned Document: {file.filename}]"))
                        conn.commit()

                    return {"status": "ok", "chunks": len(img_paths), "total_pages": total_pages,
                            "filename": file.filename, "vision": True}
                else:
                    # No Vision + Scanned PDF = Empty RAG (existing fallback)
                    print("No digital text and no Vision model active. Extracting minimal text.")
                    text = digital_text  # already sampled above
        else:
            text = content.decode("utf-8", errors="ignore")

        ext = os.path.splitext(safe_name.lower())[1]
        is_code = ext in CODE_EXTENSIONS

        if is_code:
            # For code files, we treat the entire file as a single chunk (up to 100k chars)
            chunks = [text[:100000]]
        else:
            # Standard chunking for generic text and PDFs
            chunk_size = 800
            chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)
                      if len(text[i:i + chunk_size].strip()) > 20]

        if not chunks:
            return {"status": "ok", "chunks": 0}

        emb_model = get_embedder()

        if chat_id not in state.document_store:
            state.document_store[chat_id] = []

        if emb_model:
            # For code, only embed the start of the file for indexing speed/relevance
            embs_input = [c[:500] if is_code else c for c in chunks]
            embeddings = emb_model.encode(embs_input)
            for chunk, emb in zip(chunks, embeddings):
                state.document_store[chat_id].append({
                    "type": "code" if is_code else "text",
                    "text": chunk,
                    "emb": emb,
                    "filename": safe_name if is_code else None
                })
        else:
            # Fallback: store text chunks without embeddings if embedder is dead
            print("Warning: Saving document chunks without embeddings (RAG offline).")
            for chunk in chunks:
                state.document_store[chat_id].append({
                    "type": "code" if is_code else "text",
                    "text": chunk,
                    "emb": None,
                    "filename": safe_name if is_code else None
                })

        # Record attachment in DB so it shows up in history when switching chats
        with closing(get_db_connection()) as conn:
            if not conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone():
                conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, f"Doc: {file.filename}"))
            conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                         (chat_id, "user", f"[Attached Document: {file.filename}]"))
            conn.commit()

        return {"status": "ok", "chunks": len(chunks), "filename": file.filename,
                "rag_active": emb_model is not None}
    except Exception as e:
        print(f"Error uploading doc: {e}")
        raise HTTPException(status_code=500, detail=str(e))
