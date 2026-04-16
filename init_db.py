import sqlite3
import os

DB_PATH = "database/chats.db"

def init_db():
    if not os.path.exists("database"):
        os.makedirs("database")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Tables for chats and messages
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    # 2. Table for RAG Documents (Persisting context across restarts)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        file_name TEXT,
        content TEXT NOT NULL,
        embedding BLOB,
        type TEXT DEFAULT 'text', -- 'text' or 'image' (for VLM paths)
        metadata TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
    )
    """)
    
    # 3. Table for available models
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        active BOOLEAN DEFAULT 0,
        supports_vision BOOLEAN DEFAULT 0,
        supports_image_generation BOOLEAN DEFAULT 0,
        is_downloaded BOOLEAN DEFAULT 0,
        last_used TIMESTAMP
    )
    """)

    # 4. Table for Global/User Settings
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # --- Safe Migrations for existing databases ---
    def add_column_if_missing(table, column, definition):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"Added column {column} to {table}")
        except sqlite3.OperationalError:
            pass # already exists

    add_column_if_missing("chats", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    add_column_if_missing("models", "supports_vision", "BOOLEAN DEFAULT 0")
    add_column_if_missing("models", "supports_image_generation", "BOOLEAN DEFAULT 0")
    add_column_if_missing("models", "is_downloaded", "BOOLEAN DEFAULT 0")
    add_column_if_missing("models", "last_used", "TIMESTAMP")
    add_column_if_missing("chats", "system_prompt", "TEXT DEFAULT ''")
    
    # Seed default model if no models exist
    cursor.execute("SELECT COUNT(*) FROM models")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO models (name, active, supports_vision, supports_image_generation, is_downloaded) VALUES (?, ?, ?, ?, ?)", 
                       ("mlx-community/gemma-4-e2b-it-4bit", 1, 1, 0, 0))
        cursor.execute("INSERT INTO models (name, active, supports_vision, supports_image_generation, is_downloaded) VALUES (?, ?, ?, ?, ?)", 
                       ("mlx-community/gemma-4-e4b-it-4bit", 0, 1, 0, 0))
    
    conn.commit()
    conn.close()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
