import os
import uuid
import sqlite3
import subprocess
from fastapi import FastAPI, HTTPException, Request
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

app = FastAPI()

# Global state for model and tokenizer
DEFAULT_MODEL = "mlx-community/gemma-3-4b-it-4bit-DWQ"
MODEL_NAME = None
model = None
tokenizer = None
DB_PATH = "database/chats.db"

# Track 'say' subprocesses explicitly to prevent pkill hijacking
say_processes = set()

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

@app.post("/api/chat")
async def chat_endpoint(chat_data: ChatCreate, chat_id: Optional[str] = None):
    # 1. Start or resume chat
    if not chat_id:
        chat_id = str(uuid.uuid4())
        # Use first message as title
        title = chat_data.message[:50] + "..." if len(chat_data.message) > 50 else chat_data.message
        with closing(get_db_connection()) as conn:
            conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, title))
            conn.commit()
    
    # 2. Save user message
    with closing(get_db_connection()) as conn:
        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", chat_data.message))
        conn.commit()
    
        # 3. Get history for prompt
        history = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp", (chat_id,)).fetchall()
    
    # 4. Format prompt
    messages = []
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    
    async def event_generator():
        # Yield the chat_id first so the client knows it
        yield f"data: {json.dumps({'chat_id': chat_id})}\n\n"
        
        full_response = ""
        try:
            # 5. Generate response tokens streaming
            for response in stream_generate(model, tokenizer, prompt=prompt, max_tokens=2048):
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
