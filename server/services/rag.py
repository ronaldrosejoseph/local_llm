"""
RAG (Retrieval-Augmented Generation) services.

Handles embedding model loading, document chunking, PDF-to-image conversion,
and semantic similarity retrieval for document context injection.
"""

import os
import re
import uuid
import json

import numpy as np

from server import state
from server.config import load_config
from server.db import get_db_connection
from contextlib import closing

# Code file extensions — these are included in full rather than chunked
CODE_EXTENSIONS = {
    # Web / UI
    ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte", ".astro",
    ".html", ".css", ".sass", ".scss", ".less",
    # Templating
    ".twig", ".blade", ".hbs", ".handlebars", ".ejs", ".pug", ".jade",
    # Python
    ".py",
    # Systems
    ".go", ".rs", ".cpp", ".c", ".h", ".zig", ".nim",
    # JVM / Kotlin / Scala / Groovy
    ".java", ".kt", ".scala", ".groovy", ".gradle",
    # Ruby / PHP / Swift / R / Dart
    ".rb", ".php", ".swift", ".r", ".dart",
    # Functional
    ".hs", ".ml", ".mli", ".fs", ".fsx", ".clj", ".cljs", ".ex", ".exs",
    # Scripting / Shell
    ".sh", ".bash", ".zsh", ".pl", ".pm", ".lua",
    # Data / Config / Infra
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".xml", ".csv", ".sql", ".proto",
    # GraphQL / Prisma
    ".graphql", ".gql", ".prisma",
    # Terraform / HCL / Nix
    ".tf", ".tfvars", ".hcl", ".nix",
    # Build / Meta
    ".cmake", ".makefile", ".dockerfile", ".lock", ".diff", ".patch",
    # Docs
    ".md",
}


def get_embedder():
    """Lazy-load the SentenceTransformer embedding model."""
    if state.embedder_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            # Set local_files_only=False allows fallback to cache if offline
            state.embedder_model = SentenceTransformer("all-MiniLM-L6-v2")
            print("Embedder model loaded successfully.")
        except Exception as e:
            print(f"CRITICAL: Failed to load embedder model: {e}")
            # Do not set embedder_model to anything so we can retry later
            return None
    return state.embedder_model


def pdf_to_images(pdf_content: bytes, chat_id: str, start_page: int = 0, limit: int = 5):
    """Converts PDF pages to images for Vision model consumption."""
    import fitz  # PyMuPDF

    os.makedirs("static/uploads", exist_ok=True)
    doc = fitz.open(stream=pdf_content, filetype="pdf")
    image_paths = []

    # Limit to current window for VRAM safety/latency
    for i in range(start_page, min(start_page + limit, len(doc))):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for clarity
        img_name = f"pdf_{chat_id[:8]}_{i}_{uuid.uuid4().hex[:6]}.png"
        img_path = f"static/uploads/{img_name}"
        pix.save(img_path)
        image_paths.append(img_path)

    total_pages = len(doc)
    doc.close()
    return image_paths, total_pages


def build_rag_context(chat_id: str, query_content: str):
    """
    Build RAG context string for the latest user message.

    Returns (doc_context: str, rag_meta: dict|None)
    """
    doc_context = ""
    rag_meta = None

    if chat_id not in state.document_store or not state.document_store[chat_id]:
        return doc_context, rag_meta

    try:
        emb_model = get_embedder()

        # Separate documents into code (full file) and text (chunked RAG)
        code_docs = [d for d in state.document_store[chat_id] if d.get("type") == "code"]
        text_docs = [d for d in state.document_store[chat_id] if d.get("type") == "text" and d.get("emb") is not None]

        # 1. Add Code Context (All code documents are included in full)
        if code_docs:
            doc_context += "### Full Code Context ###\n"
            for d in code_docs:
                fname = d.get('filename', 'Code File')
                doc_context += f"File: {fname}\n```\n{d['text']}\n```\n\n"

        # 2. Add Text Context (Semantic RAG search for large documents/PDFs)
        if emb_model and text_docs:
            query = query_content.replace("/web", "").strip()
            query_emb = emb_model.encode([query])[0]

            doc_embs = [d['emb'] for d in text_docs]
            q_norm = query_emb / np.linalg.norm(query_emb)
            d_norms = doc_embs / np.linalg.norm(doc_embs, axis=1)[:, np.newaxis]
            similarities = np.dot(d_norms, q_norm)

            # Sorting all available text chunks by descending similarity
            all_indices = np.argsort(similarities)[::-1]
            total_chunks = len(all_indices)

            # Windowing Logic for Text RAG
            offset = state.rag_offsets.get(chat_id, 0)
            if offset >= total_chunks:
                offset = 0

            limit = load_config().get("pdf_text_pages_per_batch", 50)
            top_indices = all_indices[offset: offset + limit]
            rag_meta = {"offset": offset, "total": total_chunks, "limit": limit, "is_vision": False}

            doc_context += f"### Relevant Document Snippets (Chunks {offset + 1} to {min(offset + limit, total_chunks)} of {total_chunks}) ###\n"
            if offset > 0:
                doc_context += f"System note: Showing NEXT relevant context snippets.\n\n"

            for idx in top_indices:
                doc_context += f"- {text_docs[idx]['text']}\n\n"

            if total_chunks > (offset + limit):
                doc_context += f"\nNote: {total_chunks - (offset + limit)} more sections available. Ask 'next' for more.\n"
        else:
            print("RAG: Text embedding retrieval skipped (likely vision PDF or missing embedder).")
    except Exception as e:
        print(f"RAG retrieval skipped: {e}")

    return doc_context, rag_meta


def handle_vision_pdf_pagination(chat_id: str):
    """
    Handle on-demand extraction of additional PDF pages for Vision models.

    Returns rag_meta dict if vision PDF is active, otherwise None.
    """
    vision_pdf = None
    if chat_id in state.document_store:
        for item in state.document_store[chat_id]:
            if item.get("type") == "pdf_metadata":
                vision_pdf = item
                break

    if not vision_pdf:
        return None

    offset = state.rag_offsets.get(chat_id, 0)
    limit = load_config().get("pdf_image_pages_per_batch", 5)
    total_pages = vision_pdf["total_pages"]

    # Extract more pages if current offset window isn't yet processed
    if (offset + limit) > vision_pdf["processed_pages"] and vision_pdf["processed_pages"] < total_pages:
        try:
            print(f"Vision: Extra extraction triggered for page range {vision_pdf['processed_pages']} to {min(offset + limit, total_pages)}...")
            with open(vision_pdf["path"], "rb") as f:
                pdf_bytes = f.read()

            new_imgs, _ = pdf_to_images(pdf_bytes, chat_id, start_page=vision_pdf["processed_pages"],
                                        limit=(offset + limit) - vision_pdf["processed_pages"])
            for p in new_imgs:
                state.document_store[chat_id].append({"type": "image", "path": p})
            vision_pdf["processed_pages"] += len(new_imgs)
        except Exception as e:
            print(f"Vision background extraction error: {e}")

    # Limit window for UI and Model
    return {"offset": offset, "total": total_pages, "limit": limit, "is_vision": True}


def save_documents_to_db(chat_id: str):
    """Persist document_store entries for a chat to the documents table."""
    if chat_id not in state.document_store:
        return

    with closing(get_db_connection()) as conn:
        # Full replace: clear existing rows for this chat
        conn.execute("DELETE FROM documents WHERE chat_id = ?", (chat_id,))

        for doc in state.document_store[chat_id]:
            doc_type = doc.get("type", "text")
            content = doc.get("text", "")
            file_name = doc.get("filename")

            # Serialize embedding as BLOB
            embedding_blob = None
            metadata = {}

            if doc.get("emb") is not None:
                embedding_blob = doc["emb"].tobytes()
                metadata["emb_shape"] = list(doc["emb"].shape)
                metadata["emb_dtype"] = str(doc["emb"].dtype)

            if doc.get("path"):
                metadata["path"] = doc["path"]
            if doc.get("total_pages"):
                metadata["total_pages"] = doc["total_pages"]
            if doc.get("processed_pages") is not None and doc_type == "pdf_metadata":
                metadata["processed_pages"] = doc["processed_pages"]

            conn.execute(
                "INSERT INTO documents (chat_id, file_name, content, embedding, type, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, file_name, content, embedding_blob, doc_type, json.dumps(metadata))
            )

        conn.commit()
    print(f"RAG: Persisted {len(state.document_store[chat_id])} documents for chat {chat_id}")


def load_documents_from_db(chat_id: str) -> bool:
    """Load documents from DB into document_store. Returns True if docs were found."""
    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            "SELECT file_name, content, embedding, type, metadata FROM documents WHERE chat_id = ? ORDER BY id",
            (chat_id,)
        ).fetchall()

    if not rows:
        return False

    state.document_store[chat_id] = []
    for row in rows:
        doc = {"type": row["type"]}

        metadata = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except Exception:
                pass

        if row["type"] in ("text", "code"):
            doc["text"] = row["content"]
            doc["filename"] = row["file_name"]
            if row["embedding"] and "emb_shape" in metadata:
                doc["emb"] = np.frombuffer(
                    row["embedding"],
                    dtype=metadata.get("emb_dtype", "float32")
                ).reshape(metadata["emb_shape"]).copy()  # .copy() to make writable
            else:
                doc["emb"] = None
        elif row["type"] == "image":
            doc["path"] = metadata.get("path", "")
        elif row["type"] == "pdf_metadata":
            doc["path"] = metadata.get("path", "")
            doc["total_pages"] = metadata.get("total_pages", 0)
            doc["processed_pages"] = metadata.get("processed_pages", 0)

        state.document_store[chat_id].append(doc)

    print(f"RAG: Loaded {len(rows)} documents from DB for chat {chat_id}")
    return True
