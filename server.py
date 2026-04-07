import os
import uuid
import sqlite3
import subprocess
import shutil
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import mlx_lm
from mlx_lm import load, generate, stream_generate
from mlx_lm.sample_utils import make_sampler, make_repetition_penalty
import mlx_vlm
from mlx_vlm.utils import load_config as load_vlm_config
from mlx_vlm.prompt_utils import apply_chat_template as apply_vlm_template
from mlx_vlm import stream_generate as stream_vlm_generate
import json
import asyncio
import queue
import threading
from huggingface_hub import HfApi
from contextlib import closing
import io
import re
import numpy as np

app = FastAPI()

# Global state for model and tokenizer
DEFAULT_MODEL = "mlx-community/gemma-4-e2b-it-4bit"
MODEL_NAME = None
model = None
tokenizer = None
processor = None # For VLM models
IS_VLM = False   # Flag if model is vision-based
DB_PATH = "database/chats.db"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB hard limit to prevent OOM on large uploads

# --- Config Management ---
CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "max_tokens": 8192,
    "temperature": 0.7,
    "top_p": 0.9,
    "repetition_penalty": 1.1,
}

def load_config() -> dict:
    """Load config from disk, falling back to defaults for any missing keys."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# Track 'say' subprocesses explicitly to prevent pkill hijacking
say_processes = set()

embedder_model = None
document_store = {} # mapping chat_id -> lists of dicts {"text": chunk, "emb": vector}
rag_offsets = {}    # mapping chat_id -> current integer offset for context windowing

def get_embedder():
    global embedder_model
    if embedder_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            # Set local_files_only=False allows fallback to cache if offline
            embedder_model = SentenceTransformer("all-MiniLM-L6-v2")
            print("Embedder model loaded successfully.")
        except Exception as e:
            print(f"CRITICAL: Failed to load embedder model: {e}")
            # Do not set embedder_model to anything so we can retry later
            return None
    return embedder_model

def pdf_to_images(pdf_content: bytes, chat_id: str):
    """Converts PDF pages to images for Vision model consumption."""
    import fitz # PyMuPDF
    import uuid
    
    os.makedirs("static/images", exist_ok=True)
    doc = fitz.open(stream=pdf_content, filetype="pdf")
    image_paths = []
    
    # Limit to first 5 pages for VRAM safety/latency
    for i in range(min(5, len(doc))):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2)) # 2x zoom for clarity
        img_name = f"pdf_{chat_id[:8]}_{i}_{uuid.uuid4().hex[:6]}.png"
        img_path = f"static/images/{img_name}"
        pix.save(img_path)
        image_paths.append(img_path)
        
    doc.close()
    return image_paths

# Database helper functions
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Model loading logic
def load_active_model(override_name: str = None) -> tuple[bool, str]:
    """Loads the specified or default model. Returns (success, actual_model_name)."""
    global MODEL_NAME, model, tokenizer, processor, IS_VLM
    
    if override_name:
        MODEL_NAME = override_name
    else:
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT name FROM models WHERE active = 1").fetchone()
        
        if not row:
            MODEL_NAME = DEFAULT_MODEL
        else:
            MODEL_NAME = row["name"]
        
    print(f"Loading model {MODEL_NAME}...")
    
    try:
        # Step 1: Attempt to load as a Vision model (VLM)
        print(f"Checking if {MODEL_NAME} is a Vision model (mlx_vlm)...")
        model, processor = mlx_vlm.load(MODEL_NAME)
        tokenizer        = processor.tokenizer # align for shared code
        IS_VLM = True
        print(f"Model {MODEL_NAME} loaded successfully as VLM.")
        return True, MODEL_NAME
    except Exception as e_vlm:
        # Step 2: If VLM load fails, attempt as a standard LLM
        print(f"Not a VLM or mlx_vlm failed ({e_vlm}). Trying as standard LLM (mlx_lm)...")
        try:
            model, tokenizer = load(MODEL_NAME)
            processor = None
            IS_VLM = False
            print(f"Model {MODEL_NAME} loaded successfully as standard LLM.")
            return True, MODEL_NAME
        except Exception as e_lm:
            # Step 3: Total Load Failure
            error_msg = str(e_lm)
            print(f"Error loading model {MODEL_NAME}: {error_msg}")
            
            # If the active model fails, try to load the default
            if MODEL_NAME != DEFAULT_MODEL:
                print(f"Falling back to default model: {DEFAULT_MODEL}")
                # Update DB to reflect fallback so UI and future loads are correct
                with closing(get_db_connection()) as conn:
                    conn.execute("UPDATE models SET active = 0")
                    conn.execute("UPDATE models SET active = 1 WHERE name = ?", (DEFAULT_MODEL,))
                    conn.commit()
                
                # Recursive call to load the default model
                _, final_name = load_active_model(override_name=DEFAULT_MODEL)
                return False, final_name
            else:
                print("CRITICAL: Both active and default models failed to load. Server will be non-functional.")
                model, tokenizer, processor = None, None, None
                return False, MODEL_NAME

# Initial load at startup
load_active_model()

class Message(BaseModel):
    role: str
    content: str

class ChatCreate(BaseModel):
    message: str

class ChatResponse(BaseModel):
    chat_id: str
    response: str

class SayRequest(BaseModel):
    text: str

class ConfigUpdate(BaseModel):
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    repetition_penalty: Optional[float] = None

@app.get("/api/chats")
def get_chats():
    # Sync route unblocks asyncio threadpool
    with closing(get_db_connection()) as conn:
        chats = conn.execute("SELECT * FROM chats ORDER BY created_at DESC").fetchall()
    return [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]} for c in chats]

@app.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: str):
    # Sync route unblocks asyncio threadpool
    with closing(get_db_connection()) as conn:
        messages = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp", (chat_id,)).fetchall()
    return [{"role": m["role"], "content": m["content"]} for m in messages]

@app.post("/api/upload-document")
async def upload_document(chat_id: str = Form(...), file: UploadFile = File(...)):
    try:
        content = await file.read()

        # Reject files that exceed the size limit before doing any processing
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File too large. Maximum upload size is {MAX_UPLOAD_BYTES // (1024*1024)}MB.")

        # Sanitize filename: strip path components and non-safe characters to prevent
        # path traversal attacks (e.g. filename='../../etc/passwd')
        raw_name = os.path.basename(file.filename or "upload")
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', raw_name) or "upload"

        # 1. Handle Vision Image Uploads
        if safe_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            os.makedirs("static/images", exist_ok=True)
            img_path = f"static/images/tmp_{uuid.uuid4().hex[:8]}_{safe_name}"
            with open(img_path, "wb") as f:
                f.write(content)
            
            if chat_id not in document_store:
                document_store[chat_id] = []
            # We flag this chunk as an image to bypass text RAG
            document_store[chat_id].append({"type": "image", "path": img_path})
            
            # Record attachment in DB so it shows up in history when switching chats
            with closing(get_db_connection()) as conn:
                # Ensure chat exists for this ID if this was the first action
                if not conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone():
                    conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, f"Image: {file.filename}"))
                conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", 
                             (chat_id, "user", f"[Attached Image: {file.filename}]"))
                conn.commit()

            return {"status": "ok", "chunks": 1, "filename": file.filename}
            
        # 2. Handle Text Docs and Code Files for RAG
        # PDFs are parsed page-by-page; everything else is read as UTF-8
        text = ""
        if safe_name.lower().endswith(".pdf"):
            import io
            from PyPDF2 import PdfReader
            
            # Hybrid Logic: Check if PDF has a digital text layer
            print(f"Analyzing PDF: {file.filename} for digital text...")
            pdf = PdfReader(io.BytesIO(content))
            digital_text = ""
            # Sampling first 3 pages for a quick "has-text" check
            for page in pdf.pages[:3]:
                t = page.extract_text()
                if t: digital_text += t
            
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
                if IS_VLM:
                    print("No digital text found (scanned): converting PDF to images via fitz...")
                    img_paths = pdf_to_images(content, chat_id)
                    if chat_id not in document_store:
                        document_store[chat_id] = []
                    for p in img_paths:
                        document_store[chat_id].append({"type": "image", "path": p})
                    
                    with closing(get_db_connection()) as conn:
                        if not conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone():
                            conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, f"Doc: {file.filename}"))
                        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", 
                                     (chat_id, "user", f"[Attached Scanned Document: {file.filename}]"))
                        conn.commit()

                    return {"status": "ok", "chunks": len(img_paths), "filename": file.filename, "vision": True}
                else:
                    # No Vision + Scanned PDF = Empty RAG (existing fallback)
                    print("No digital text and no Vision model active. Extracting minimal text.")
                    text = digital_text # already sampled above
        else:
            text = content.decode("utf-8", errors="ignore")

        # Use a smaller chunk size for code files so chunks align closer to
        # function/class boundaries rather than cutting mid-line
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
        ext = os.path.splitext(safe_name.lower())[1]
        chunk_size = 400 if ext in CODE_EXTENSIONS else 800
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size) if len(text[i:i+chunk_size].strip()) > 20]
        
        if not chunks:
             return {"status": "ok", "chunks": 0}
             
        emb_model = get_embedder()
        
        if chat_id not in document_store:
            document_store[chat_id] = []

        if emb_model:
            embeddings = emb_model.encode(chunks)
            for chunk, emb in zip(chunks, embeddings):
                document_store[chat_id].append({"type": "text", "text": chunk, "emb": emb})
        else:
            # Fallback: store text chunks without embeddings if embedder is dead
            print("Warning: Saving document chunks without embeddings (RAG offline).")
            for chunk in chunks:
                document_store[chat_id].append({"type": "text", "text": chunk, "emb": None})

        # Record attachment in DB so it shows up in history when switching chats
        with closing(get_db_connection()) as conn:
            if not conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone():
                conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, f"Doc: {file.filename}"))
            conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", 
                         (chat_id, "user", f"[Attached Document: {file.filename}]"))
            conn.commit()

        return {"status": "ok", "chunks": len(chunks), "filename": file.filename, "rag_active": emb_model is not None}
    except Exception as e:
        print(f"Error uploading doc: {e}")
        # Only crash if it's something other than a network/model load error if possible
        # but 500 is safer than letting the server hang
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
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
        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", chat_data.message))
        conn.commit()
    
        # 3. Get history for prompt
        history = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp", (chat_id,)).fetchall()
    
    # 4. Format prompt
    # 2b. Check for Web Search Triggering
    message_content = chat_data.message
    
    # --- RAG Pagination Detection ---
    is_next_command = False
    # Detect patterns like "next 50", "/next", "more context", "next page"
    if re.search(r'(\bnext\b|\bmore\b)\s+(\b50\b|\bcontext\b|\bpage\b|\bchunks\b)', message_content.lower()) or message_content.strip().startswith('/next'):
        is_next_command = True
        rag_offsets[chat_id] = rag_offsets.get(chat_id, 0) + 50
        print(f"Pagination triggered for {chat_id}: New offset = {rag_offsets[chat_id]}")
    else:
        # Reset pagination for any new specific question to keep relevance high
        if chat_id in rag_offsets:
            print(f"New query detected, resetting RAG offset for {chat_id}.")
            rag_offsets[chat_id] = 0
    
    # 2c. Check for Image Generation Triggering
    if message_content.strip().startswith("/imagine"):
        prompt = message_content.strip()[8:].strip()
        print(f"Triggering image generation for: {prompt}")
        
        async def image_generator():
            yield f'data: {json.dumps({"chat_id": chat_id})}\n\n'
            yield f'data: {json.dumps({"content": "### 🎨 Diffusers Pipeline Active\n\n**Booting Apple Silicon GPUs...**"})}\n\n'
            
            import threading
            import queue
            import asyncio
            q = queue.Queue()
            img_name = f"gen_{uuid.uuid4().hex[:8]}.png"
            
            def generation_thread():
                try:
                    from mflux.models.common.config import ModelConfig
                    from mflux.models.flux.variants.txt2img.flux import Flux1
                    from mflux.callbacks.callback import InLoopCallback
                    import os
                    import time
                    
                    global model, tokenizer
                    model = None
                    tokenizer = None
                    import gc; gc.collect()
                    import mlx.core as mx; mx.metal.clear_cache()
                    
                    if not os.path.exists("static/images"):
                        os.makedirs("static/images")
                        
                    class ProgressCB(InLoopCallback):
                        def call_in_loop(self, t, seed, prompt, latents, config, time_steps, **kwargs):
                            if time_steps and time_steps.total > 0:
                                progress = int((time_steps.n / time_steps.total) * 100)
                                q.put({"progress": progress})
                                
                    flux = Flux1(
                        model_config=ModelConfig.from_name(model_name="schnell"),
                        quantize=4
                    )
                    flux.callbacks.register(ProgressCB())
                    
                    image = flux.generate_image(
                        seed=int(time.time()),
                        prompt=prompt,
                        num_inference_steps=4,
                        width=720,
                        height=720
                    )
                    
                    img_path = f"static/images/{img_name}"
                    image.save(path=img_path)
                    
                    del flux
                    import gc; gc.collect()
                    import mlx.core as mx; mx.metal.clear_cache()
                    load_active_model()
                    
                    markdown_img = f"![{prompt}](/images/{img_name})\n"
                    assistant_full_reply = f"Here is the image you requested:\n\n{markdown_img}"
                    with closing(get_db_connection()) as conn:
                        conn.execute(
                            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                            (chat_id, "assistant", assistant_full_reply)
                        )
                        conn.commit()
                        
                    q.put({"image": img_name})
                except Exception as e:
                    q.put({"error": str(e)})

            threading.Thread(target=generation_thread).start()
            
            def get_ascii_bar(pct, length=20):
                filled = int((pct / 100) * length)
                return "█" * filled + "░" * (length - filled)
                
            while True:
                try:
                    msg = q.get_nowait()
                    if "progress" in msg:
                        pct = msg["progress"]
                        bar = get_ascii_bar(pct)
                        status = f"### 🎨 Diffusers Pipeline Active\n\n**Generating image natively...**\n\n`{bar}` **{pct}%**\n\n*(Processing tensors...)*"
                        yield f'data: {json.dumps({"replace": status})}\n\n'
                    elif "image" in msg:
                        yield f'data: {json.dumps({"replace": f"![Generated Image](/images/{msg['image']})"})}\n\n'
                        break
                    elif "error" in msg:
                        yield f'data: {json.dumps({"replace": f"**Error:** {msg['error']}"})}\n\n'
                        break
                except queue.Empty:
                    await asyncio.sleep(0.2)
                    
            yield 'data: [DONE]\n\n'
            
        return StreamingResponse(image_generator(), media_type="text/event-stream")
        
    # 2d. Check for Image-to-Image Editing Triggering
    if message_content.strip().startswith("/edit"):
        prompt = message_content.strip()[5:].strip()
        print(f"Triggering image editing for: {prompt}")
        
        # Verify an image has been uploaded to this session
        has_image = False
        source_image_path = None
        if chat_id in document_store:
            # Grab newest uploaded image natively
            for doc in reversed(document_store[chat_id]):
                if doc.get("type") == "image":
                    has_image = True
                    source_image_path = doc["path"]
                    break
                    
        async def image_edit_generator():
            yield f'data: {json.dumps({"chat_id": chat_id})}\n\n'
            
            if not has_image or not source_image_path:
                yield f'data: {json.dumps({"content": "**Error:** You must attach an image using the paperclip icon before using `/edit`."})}\n\n'
                yield 'data: [DONE]\n\n'
                return
                
            yield f'data: {json.dumps({"content": "### 🎨 Diffusers Img2Img Active\n\n**Booting Apple Silicon GPUs...**"})}\n\n'
            
            import threading
            import queue
            import asyncio
            q = queue.Queue()
            img_name = f"edit_{uuid.uuid4().hex[:8]}.png"
            
            def edit_thread():
                try:
                    from mflux.models.common.config import ModelConfig
                    from mflux.models.flux.variants.txt2img.flux import Flux1
                    from mflux.callbacks.callback import InLoopCallback
                    import os
                    import time
                    
                    global model, tokenizer
                    model = None
                    tokenizer = None
                    import gc; gc.collect()
                    import mlx.core as mx; mx.metal.clear_cache()
                    
                    if not os.path.exists("static/images"):
                        os.makedirs("static/images")
                        
                    class ProgressCB(InLoopCallback):
                        def call_in_loop(self, t, seed, prompt, latents, config, time_steps, **kwargs):
                            if time_steps and time_steps.total > 0:
                                progress = int((time_steps.n / time_steps.total) * 100)
                                q.put({"progress": progress})
                                
                    flux = Flux1(
                        model_config=ModelConfig.from_name(model_name="schnell"),
                        quantize=4
                    )
                    flux.callbacks.register(ProgressCB())
                    
                    image = flux.generate_image(
                        seed=int(time.time()),
                        prompt=prompt,
                        num_inference_steps=8,
                        width=720,
                        height=720,
                        image_path=source_image_path,
                        image_strength=0.15
                    )
                    
                    img_path = f"static/images/{img_name}"
                    image.save(path=img_path)
                    
                    del flux
                    import gc; gc.collect()
                    import mlx.core as mx; mx.metal.clear_cache()
                    load_active_model()
                    
                    markdown_img = f"![{prompt}](/images/{img_name})\n"
                    assistant_full_reply = f"Here is your edited image:\n\n{markdown_img}"
                    with closing(get_db_connection()) as conn:
                        conn.execute(
                            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                            (chat_id, "assistant", assistant_full_reply)
                        )
                        conn.commit()
                        
                    q.put({"image": img_name})
                except Exception as e:
                    q.put({"error": str(e)})

            threading.Thread(target=edit_thread).start()
            
            def get_ascii_bar(pct, length=20):
                filled = int((pct / 100) * length)
                return "█" * filled + "░" * (length - filled)
                
            while True:
                try:
                    msg = q.get_nowait()
                    if "progress" in msg:
                        pct = msg["progress"]
                        bar = get_ascii_bar(pct)
                        status = f"### 🎨 Diffusers Img2Img Active\n\n**Editing image natively...**\n\n`{bar}` **{pct}%**\n\n*(Transforming matrices...)*"
                        yield f'data: {json.dumps({"replace": status})}\n\n'
                    elif "image" in msg:
                        yield f'data: {json.dumps({"replace": f"![Edited Image](/images/{msg['image']})"})}\n\n'
                        break
                    elif "error" in msg:
                        yield f'data: {json.dumps({"replace": f"**Error:** {msg['error']}"})}\n\n'
                        break
                except queue.Empty:
                    await asyncio.sleep(0.2)
                    
            yield 'data: [DONE]\n\n'
            
        return StreamingResponse(image_edit_generator(), media_type="text/event-stream")
    
    messages = []
    
    web_context = ""
    last_msg = history[-1]["content"] if history else ""
    if message_content.strip().startswith("/web"):
        query = message_content.strip()[4:].strip()
        print(f"Triggering web search for: {query}")
        
        # Hack for weather queries since DDG text limits widget scraping
        if "weather" in query.lower():
            loc = "".join(query.lower().split("weather")[1:]).replace("in", "").replace("like", "").replace("for", "").replace("?", "").replace("right now", "").strip()
            if loc:
                try:
                    import urllib.request, urllib.parse
                    req = urllib.request.Request(f"https://wttr.in/{urllib.parse.quote(loc)}?format=3", headers={'User-Agent': 'curl'})
                    w_res = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
                    web_context += f"### Live Weather Widget Data ###\nLocation/Weather: {w_res.strip()}\n\n"
                except Exception as e:
                    print("wttr.in fail:", e)
                    
        try:
            import urllib.request, urllib.parse
            url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36"})
            html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")
            
            snippets = re.findall(r"<a class=\"result__snippet[^>]*>(.*?)</a>", html, re.DOTALL | re.IGNORECASE)
            
            if snippets:
                web_context += "### Live Web Search Context ###\n"
                for s in snippets[:3]:
                    clean_text = re.sub(r"<[^>]+>", "", s).strip()
                    clean_text = clean_text.replace("&#x27;", "'").replace("&quot;", '"')
                    web_context += f"Snippet: {clean_text}\n\n"
        except Exception as e:
            print(f"Web search failed: {e}")
            
        if not web_context:
            web_context = "System Note: Live Web search is currently temporarily blocked or failing. Tell the user you couldn't access the live web context right now."
    
    # Hoist RAG status variables for event_generator
    rag_meta = None
    
    for i, h in enumerate(history):
        content = h["content"]
        
        # Inject RAG / Web Context into the latest user message
        if i == len(history) - 1:
            doc_context = ""
            if chat_id in document_store and document_store[chat_id]:
                try:
                    emb_model = get_embedder()
                    if not emb_model:
                        raise Exception("Embedder model not available.")
                        
                    query = content.replace("/web", "").strip()
                    query_emb = emb_model.encode([query])[0]
                    
                    docs = [d for d in document_store[chat_id] if d.get("emb") is not None]
                    if docs:
                        doc_embs = [d['emb'] for d in docs]
                        
                        q_norm = query_emb / np.linalg.norm(query_emb)
                        d_norms = doc_embs / np.linalg.norm(doc_embs, axis=1)[:, np.newaxis]
                        similarities = np.dot(d_norms, q_norm)
                        
                        # Sorting all available chunks by descending similarity
                        all_indices = np.argsort(similarities)[::-1]
                        total_chunks = len(all_indices)
                        
                        # Windowing Logic
                        offset = rag_offsets.get(chat_id, 0)
                        # Ensure we don't exceed the total length
                        if offset >= total_chunks: offset = 0 
                        
                        limit = 50
                        top_indices = all_indices[offset : offset + limit]
                        rag_meta = {"offset": offset, "total": total_chunks, "limit": limit}
                        
                        doc_context = f"### Attached Document Context (Chunks {offset+1} to {min(offset+limit, total_chunks)} of {total_chunks}) ###\n"
                        if offset > 0:
                            doc_context += f"System note: You are viewing the NEXT set of relevant context. Previous {offset} chunks have been hidden to save memory.\n\n"
                        
                        for idx in top_indices:
                            doc_context += f"- {docs[idx]['text']}\n\n"
                        
                        if total_chunks > (offset + limit):
                            doc_context += f"\nNote to model: There are {total_chunks - (offset + limit)} more chunks available. If the user asks for 'more' or 'next', inform them that you can advance the context window.\n"
                    else:
                        print("RAG: No indexable documents found for this chat.")
                except Exception as e:
                    print(f"RAG retrieval skipped: {e}")

            combined_context = web_context + ("\n" if web_context and doc_context else "") + doc_context
            if combined_context:
                content = f"{combined_context}\nInstructions: Utilizing the context provided above, answer the following query:\n\n{content.replace('/web', '').strip()}"
                
        messages.append({"role": h["role"], "content": content})
    
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    
    async def event_generator():
        # Yield metadata first: chat_id and RAG window info
        yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
        if rag_meta:
            yield f"data: {json.dumps({'rag_status': rag_meta})}\n\n"
        
        full_response = ""
        try:
            cfg = load_config()

            if IS_VLM:
                # --- VLM Branch ---
                # Check for images in document_store
                image_paths = []
                if chat_id in document_store:
                    image_paths = [d["path"] for d in document_store[chat_id] if d.get("type") == "image"]
                
                # VLM prompt formatting needs the MESSAGES list, not the pre-templated string
                formatted_prompt = apply_vlm_template(
                    processor,
                    load_vlm_config(MODEL_NAME),
                    messages, # Use the original messages list
                    num_images=len(image_paths)
                )
                
                for response in stream_vlm_generate(
                    model,
                    processor,
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
                    model, tokenizer,
                    prompt=prompt,
                    max_tokens=cfg["max_tokens"],
                    sampler=sampler,
                    logits_processors=logits_processors,
                ):
                    full_response += response.text
                    yield f"data: {json.dumps({'content': response.text})}\n\n"
                    await asyncio.sleep(0) # Yield control
                
        except asyncio.CancelledError:
            print(f"Chat generation for {chat_id} was cancelled by client.")
        except Exception as e:
            print(f"Error during generation: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # 6. Save assistant message even if partially generated
            if full_response:
                with closing(get_db_connection()) as conn:
                    conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", full_response))
                    conn.commit()
            
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/say")
async def say_endpoint(data: SayRequest):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
        
    # Prevent injection of command options
    if text.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid text content for speech.")
    
    try:
        # Spawn tracked process
        proc = subprocess.Popen(["say", text])
        say_processes.add(proc)
        
        # Periodically clean up finished processes avoiding memory leak
        for p in list(say_processes):
            if p.poll() is not None:
                say_processes.remove(p)
                
        return {"status": "ok"}
    except Exception as e:
        print(f"Error in say_endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stop-say")
def stop_say_endpoint():
    try:
        terminated = 0
        for p in list(say_processes):
            if p.poll() is None:
                p.terminate()
                terminated += 1
            say_processes.remove(p)
            
        return {"status": "ok", "terminated": terminated}
    except Exception as e:
        print(f"Error in stop_say_endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
    return {"status": "ok"}

# --- Config API ---

@app.get("/api/config")
def get_config():
    return load_config()

@app.patch("/api/config")
def update_config(data: ConfigUpdate):
    cfg = load_config()
    if data.max_tokens is not None:
        cfg["max_tokens"] = max(256, min(32768, int(data.max_tokens)))
    if data.temperature is not None:
        cfg["temperature"] = round(max(0.0, min(2.0, data.temperature)), 2)
    if data.top_p is not None:
        cfg["top_p"] = round(max(0.0, min(1.0, data.top_p)), 2)
    if data.repetition_penalty is not None:
        cfg["repetition_penalty"] = round(max(1.0, min(1.5, data.repetition_penalty)), 2)
    save_config(cfg)
    return cfg

# --- Model APIs ---

class ModelAdd(BaseModel):
    name: str

@app.get("/api/models")
def get_models():
    with closing(get_db_connection()) as conn:
        models = conn.execute("SELECT id, name, active FROM models").fetchall()
    return [{"id": m["id"], "name": m["name"], "active": bool(m["active"])} for m in models]

@app.post("/api/models")
def add_model(model_data: ModelAdd):
    if "mlx-community" not in model_data.name:
        raise HTTPException(status_code=400, detail="Model must be from mlx-community")
    
    # Verify model existence on Hugging Face
    try:
        api = HfApi()
        api.model_info(repo_id=model_data.name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Model '{model_data.name}' not found on Hugging Face. Please check the name.")
    
    with closing(get_db_connection()) as conn:
        try:
            conn.execute("INSERT INTO models (name) VALUES (?)", (model_data.name,))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Model already exists")
    return {"status": "ok"}

@app.post("/api/models/active")
async def set_active_model(model_data: ModelAdd):
    """SSE stream that reports download/load progress while switching models."""
    global model, tokenizer, MODEL_NAME
    prev_model_name = MODEL_NAME

    # Verify model exists in DB first
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT name FROM models WHERE name = ?", (model_data.name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")

    # Detect cache state BEFORE starting the load thread.
    # HF stores blobs in: ~/.cache/huggingface/hub/models--{org}--{name}/blobs/
    safe      = model_data.name.replace("/", "--")
    cache_dir = os.path.join(os.path.expanduser("~/.cache/huggingface/hub"), f"models--{safe}")
    blobs_dir = os.path.join(cache_dir, "blobs")
    snapshots_dir = os.path.join(cache_dir, "snapshots")
    is_cached = os.path.isdir(snapshots_dir)

    result_q: queue.Queue = queue.Queue()

    def load_thread():
        global MODEL_NAME, model, tokenizer, processor, IS_VLM
        try:
            # Update DB active flag optimistically (load_active_model will correct it on failure)
            with closing(get_db_connection()) as conn:
                conn.execute("UPDATE models SET active = 0")
                conn.execute("UPDATE models SET active = 1 WHERE name = ?", (model_data.name,))
                conn.commit()
            
            # Use the centralized helper to handle VLM vs LLM and global state
            success, actual_name = load_active_model(override_name=model_data.name)
            
            payload = {
                "status": "ready",
                "model": actual_name.split("/")[-1],
                "full": actual_name
            }
            if not success:
                payload["fallback"] = True
                payload["requested"] = model_data.name
                payload["error"] = "Model failed to load and was reverted to default." # generic error for UI
            
            result_q.put(payload)
        except Exception as e:
            # Restore previous active model in DB
            try:
                with closing(get_db_connection()) as conn:
                    conn.execute("UPDATE models SET active = 0")
                    if prev_model_name:
                        conn.execute("UPDATE models SET active = 1 WHERE name = ?", (prev_model_name,))
                    conn.commit()
            except Exception:
                pass
            result_q.put({"status": "error", "message": f"Critical error during model switch: {str(e)}"})

    threading.Thread(target=load_thread, daemon=True).start()

    async def status_stream():
        loading_notified = is_cached

        if is_cached:
            yield f"data: {json.dumps({'status': 'loading', 'message': 'Loading model into memory...'})}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'downloading', 'message': 'Downloading model files...'})}\n\n"

        while True:
            # Check if the load thread finished
            try:
                msg = result_q.get_nowait()
                yield f"data: {json.dumps(msg)}\n\n"
                yield "data: [DONE]\n\n"
                return
            except queue.Empty:
                pass

            if not loading_notified:
                # Report download progress by counting blobs
                try:
                    if os.path.isdir(blobs_dir):
                        all_blobs   = [f for f in os.listdir(blobs_dir) if not f.endswith('.lock')]
                        incomplete  = [f for f in all_blobs if f.endswith('.incomplete')]
                        complete    = len(all_blobs) - len(incomplete)
                        total       = len(all_blobs)
                        if total > 0:
                            progress_msg = f"Downloading... ({complete}/{total} files)"
                            yield f"data: {json.dumps({'status': 'downloading', 'message': progress_msg})}\n\n"
                        # Transition to "loading" once no incomplete files remain
                        if total > 0 and len(incomplete) == 0:
                            loading_notified = True
                            yield f"data: {json.dumps({'status': 'loading', 'message': 'Loading model into memory...'})}\n\n"
                except Exception:
                    pass

            await asyncio.sleep(0.8)

    return StreamingResponse(status_stream(), media_type="text/event-stream")


@app.delete("/api/models/{model_name:path}")
def delete_model(model_name: str):
    global MODEL_NAME

    # Refuse to delete the currently loaded model
    if model_name == MODEL_NAME:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the active model. Switch to another model first."
        )

    # Remove from DB
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT name FROM models WHERE name = ?", (model_name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found in database.")
        conn.execute("DELETE FROM models WHERE name = ?", (model_name,))
        conn.commit()

    # Delete from HuggingFace hub cache.
    # HF stores repos as: ~/.cache/huggingface/hub/models--{org}--{model}
    # where every '/' in the repo name is replaced by '--'.
    hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
    safe_name = model_name.replace("/", "--")
    cache_dir = os.path.join(hf_cache, f"models--{safe_name}")

    deleted_from_disk = False
    if os.path.isdir(cache_dir):
        try:
            shutil.rmtree(cache_dir)
            deleted_from_disk = True
            print(f"Deleted model cache: {cache_dir}")
        except Exception as e:
            print(f"Warning: could not delete cache at {cache_dir}: {e}")
    else:
        print(f"No cache dir found at {cache_dir} — only removed from DB.")

    return {
        "status": "ok",
        "model": model_name,
        "deleted_from_disk": deleted_from_disk,
        "cache_path": cache_dir,
    }

# Serve static files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
