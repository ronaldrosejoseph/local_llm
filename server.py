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

app = FastAPI()

# Global state for model and tokenizer
MODEL_NAME = None
model = None
tokenizer = None
DB_PATH = "database/chats.db"

# Database helper functions
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Model loading logic
def load_active_model():
    global MODEL_NAME, model, tokenizer
    conn = get_db_connection()
    row = conn.execute("SELECT name FROM models WHERE active = 1").fetchone()
    conn.close()
    
    if not row:
        # Fallback to default if no active model is set
        MODEL_NAME = "mlx-community/gemma-3-4b-it-4bit-DWQ"
    else:
        MODEL_NAME = row["name"]
        
    print(f"Loading model {MODEL_NAME}...")
    try:
        model, tokenizer = load(MODEL_NAME)
        print(f"Model {MODEL_NAME} loaded successfully.")
    except Exception as e:
        print(f"Error loading model {MODEL_NAME}: {e}")
        # If the active model fails, try to load the default
        if MODEL_NAME != "mlx-community/gemma-3-4b-it-4bit-DWQ":
            MODEL_NAME = "mlx-community/gemma-3-4b-it-4bit-DWQ"
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

@app.get("/api/chats")
async def get_chats():
    conn = get_db_connection()
    chats = conn.execute("SELECT * FROM chats ORDER BY created_at DESC").fetchall()
    conn.close()
    return [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]} for c in chats]

@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str):
    conn = get_db_connection()
    messages = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp", (chat_id,)).fetchall()
    conn.close()
    return [{"role": m["role"], "content": m["content"]} for m in messages]

@app.post("/api/chat")
async def chat_endpoint(chat_data: ChatCreate, chat_id: Optional[str] = None):
    # 1. Start or resume chat
    if not chat_id:
        chat_id = str(uuid.uuid4())
        # Use first message as title
        title = chat_data.message[:50] + "..." if len(chat_data.message) > 50 else chat_data.message
        conn = get_db_connection()
        conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, title))
        conn.commit()
        conn.close()
    
    # 2. Save user message
    conn = get_db_connection()
    conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", chat_data.message))
    conn.commit()
    
    # 3. Get history for prompt
    history = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp", (chat_id,)).fetchall()
    conn.close()
    
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
                conn = get_db_connection()
                conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", full_response))
                conn.commit()
                conn.close()
            
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/say")
async def say_endpoint(request: Request):
    data = await request.json()
    text = data.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    try:
        # Using Popen makes it non-blocking so the API returns immediately
        # while the speech sounds in the background.
        subprocess.Popen(["say", text])
        return {"status": "ok"}
    except Exception as e:
        print(f"Error in say_endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stop-say")
async def stop_say_endpoint():
    try:
        # 1. Try pkill first (often more reliable on macOS)
        print("Stopping 'say' processes...")
        res = subprocess.run(["pkill", "say"], capture_output=True, text=True)
        print(f"pkill Result: {res.returncode}, Stdout: {res.stdout}, Stderr: {res.stderr}")
        
        # 2. Fallback to killall if pkill didn't find anything
        if res.returncode != 0:
            res2 = subprocess.run(["killall", "say"], capture_output=True, text=True)
            print(f"killall Result: {res2.returncode}, Stdout: {res2.stdout}, Stderr: {res2.stderr}")
            
        return {"status": "ok", "pkill_code": res.returncode}
    except Exception as e:
        print(f"Error in stop_say_endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    conn = get_db_connection()
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# --- Model APIs ---

class ModelAdd(BaseModel):
    name: str

@app.get("/api/models")
async def get_models():
    conn = get_db_connection()
    models = conn.execute("SELECT id, name, active FROM models").fetchall()
    conn.close()
    return [{"id": m["id"], "name": m["name"], "active": bool(m["active"])} for m in models]

@app.post("/api/models")
async def add_model(model_data: ModelAdd):
    if "mlx-community" not in model_data.name:
        raise HTTPException(status_code=400, detail="Model must be from mlx-community")
    
    # Verify model existence on Hugging Face
    try:
        api = HfApi()
        api.model_info(repo_id=model_data.name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Model '{model_data.name}' not found on Hugging Face. Please check the name.")
    
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO models (name) VALUES (?)", (model_data.name,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Model already exists")
    finally:
        if conn:
            conn.close()
    return {"status": "ok"}

@app.post("/api/models/active")
async def set_active_model(model_data: ModelAdd):
    global model, tokenizer, MODEL_NAME
    conn = get_db_connection()
    
    # Check if model exists
    row = conn.execute("SELECT name FROM models WHERE name = ?", (model_data.name,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Update active status in DB
    conn.execute("UPDATE models SET active = 0")
    conn.execute("UPDATE models SET active = 1 WHERE name = ?", (model_data.name,))
    conn.commit()
    conn.close()
    
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
