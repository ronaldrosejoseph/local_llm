import sqlite3
import os

DB_PATH = "database/chats.db"

def init_db():
    if not os.path.exists("database"):
        os.makedirs("database")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tables for chats and messages
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
    )
    """)
    
    # Table for available models
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        active BOOLEAN DEFAULT 0
    )
    """)
    
    # Seed default model if no models exist
    cursor.execute("SELECT COUNT(*) FROM models")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO models (name, active) VALUES (?, ?)", 
                       ("mlx-community/gemma-3-4b-it-4bit-DWQ", 1))
    
    conn.commit()
    conn.close()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
