"""
Database connection helper.
"""

import sqlite3

DB_PATH = "database/chats.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
