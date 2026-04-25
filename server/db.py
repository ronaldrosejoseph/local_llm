"""
Database connection helper.
"""

import sqlite3

DB_PATH = "database/chats.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA foreign_keys = ON;')
    return conn

def reset_to_default_model():
    """Reset the active model to a safe default in case of a crash recovery."""
    conn = get_db_connection()
    try:
        # Default safe model
        default_model = "mlx-community/gemma-4-e2b-it-4bit"
        
        # Deactivate all
        conn.execute("UPDATE models SET active = 0")
        
        # Activate default
        conn.execute("UPDATE models SET active = 1 WHERE name = ?", (default_model,))
        
        conn.commit()
        print(f"Database: Recovered! Reset active model to {default_model}")
    except Exception as e:
        print(f"Database: Error during crash recovery: {e}")
    finally:
        conn.close()
