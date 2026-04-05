import os
import uuid
import sqlite3
import subprocess
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import mlx_lm
from mlx_lm import load, generate, stream_generate
import json
import asyncio
from huggingface_hub import HfApi
from contextlib import closing
import io
import numpy as np

app = FastAPI()

# Global state for model and tokenizer
DEFAULT_MODEL = "mlx-community/Llama-3.2-1B-Instruct-4bit"
MODEL_NAME = None
model = None
tokenizer = None
DB_PATH = "database/chats.db"

# Track 'say' subprocesses explicitly to prevent pkill hijacking
say_processes = set()

embedder_model = None
document_store = {} # mapping chat_id -> lists of dicts {"text": chunk, "emb": vector}

def get_embedder():
    global embedder_model
    if embedder_model is None:
        from sentence_transformers import SentenceTransformer
        embedder_model = SentenceTransformer("all-MiniLM-L6-v2")
    return embedder_model

# Database helper functions
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Model loading logic
def load_active_model():
    global MODEL_NAME, model, tokenizer
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT name FROM models WHERE active = 1").fetchone()
    
    if not row:
        # Fallback to default if no active model is set
        MODEL_NAME = DEFAULT_MODEL
    else:
        MODEL_NAME = row["name"]
        
    print(f"Loading model {MODEL_NAME}...")
    try:
        model, tokenizer = load(MODEL_NAME)
        print(f"Model {MODEL_NAME} loaded successfully.")
    except Exception as e:
        print(f"Error loading model {MODEL_NAME}: {e}")
        # If the active model fails, try to load the default
        if MODEL_NAME != DEFAULT_MODEL:
            MODEL_NAME = DEFAULT_MODEL
            print(f"Falling back to default model: {MODEL_NAME}")
            model, tokenizer = load(MODEL_NAME)

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
        
        # 1. Handle Vision Image Uploads
        if file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            import os
            if not os.path.exists("static/images"):
                os.makedirs("static/images")
            img_path = f"static/images/tmp_{uuid.uuid4().hex[:8]}_{file.filename}"
            with open(img_path, "wb") as f:
                f.write(content)
            
            if chat_id not in document_store:
                document_store[chat_id] = []
            # We flag this chunk as an image to bypass text RAG
            document_store[chat_id].append({"type": "image", "path": img_path})
            return {"status": "ok", "chunks": 1, "filename": file.filename}
            
        # 2. Handle Text Docs for RAG
        text = ""
        if file.filename.lower().endswith(".pdf"):
            from PyPDF2 import PdfReader
            pdf = PdfReader(io.BytesIO(content))
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
        else:
            text = content.decode("utf-8", errors="ignore")
            
        chunk_size = 800
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size) if len(text[i:i+chunk_size].strip()) > 50]
        
        if not chunks:
             return {"status": "ok", "chunks": 0}
             
        model = get_embedder()
        embeddings = model.encode(chunks)
        
        if chat_id not in document_store:
            document_store[chat_id] = []
            
        for chunk, emb in zip(chunks, embeddings):
            document_store[chat_id].append({"type": "text", "text": chunk, "emb": emb})
            
        return {"status": "ok", "chunks": len(chunks), "filename": file.filename}
    except Exception as e:
        print(f"Error uploading doc: {e}")
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
            import urllib.request, urllib.parse, re
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
    
    for i, h in enumerate(history):
        content = h["content"]
        
        # Inject RAG / Web Context into the latest user message
        if i == len(history) - 1:
            doc_context = ""
            if chat_id in document_store and document_store[chat_id]:
                try:
                    emb_model = get_embedder()
                    query = content.replace("/web", "").strip()
                    query_emb = emb_model.encode([query])[0]
                    
                    docs = document_store[chat_id]
                    doc_embs = [d['emb'] for d in docs]
                    
                    q_norm = query_emb / np.linalg.norm(query_emb)
                    d_norms = doc_embs / np.linalg.norm(doc_embs, axis=1)[:, np.newaxis]
                    similarities = np.dot(d_norms, q_norm)
                    
                    top_k = min(3, len(similarities))
                    top_indices = np.argsort(similarities)[-top_k:][::-1]
                    
                    doc_context = "### Attached Document Context ###\n"
                    for idx in top_indices:
                        doc_context += f"- {docs[idx]['text']}\n\n"
                except Exception as e:
                    print(f"RAG retrieval failed: {e}")

            combined_context = web_context + ("\n" if web_context and doc_context else "") + doc_context
            if combined_context:
                content = f"{combined_context}\nInstructions: Utilizing the context provided above, answer the following query:\n\n{content.replace('/web', '').strip()}"
                
        messages.append({"role": h["role"], "content": content})
    
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    
    async def event_generator():
        # Yield the chat_id first so the client knows it
        yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
        
        full_response = ""
        try:
            # 5. Generate response tokens streaming
            for response in stream_generate(model, tokenizer, prompt=prompt, max_tokens=8192):
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
def set_active_model(model_data: ModelAdd):
    global model, tokenizer, MODEL_NAME
    
    with closing(get_db_connection()) as conn:
        # Check if model exists
        row = conn.execute("SELECT name FROM models WHERE name = ?", (model_data.name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")
        
        # Update active status in DB
        conn.execute("UPDATE models SET active = 0")
        conn.execute("UPDATE models SET active = 1 WHERE name = ?", (model_data.name,))
        conn.commit()
    
    # Reload model
    print(f"Switching to model {model_data.name}...")
    try:
        # Re-load model (this unloads the previous one automatically by reassignment)
        new_model, new_tokenizer = load(model_data.name)
        model = new_model
        tokenizer = new_tokenizer
        MODEL_NAME = model_data.name
        print(f"Switched to {MODEL_NAME}")
        return {"status": "ok", "current_model": MODEL_NAME}
    except Exception as e:
        print(f"Failed to switch model: {e}")
        # Try to restore the previous or default model
        load_active_model()
        raise HTTPException(status_code=500, detail=f"Failed to load model: {str(e)}")

# Serve static files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
