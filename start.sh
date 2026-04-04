#!/bin/bash

# start.sh - Starts the LLM server in the background

PID_FILE="server.pid"
LOG_FILE="server.log"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null; then
        echo "Server is already running (PID: $PID)"
        exit 0
    else
        echo "Stale PID file found. Removing..."
        rm "$PID_FILE"
    fi
fi

echo "Starting LLM server..."

# 1. Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating venv..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create venv. Please make sure python3 is installed."
        exit 1
    fi
fi

# 2. Install/Update requirements
if [ -f "requirements.txt" ]; then
    echo "Checking/Installing requirements..."
    ./venv/bin/pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "Error: Failed to install requirements."
        exit 1
    fi
fi

# 3. Initialize database if missing
if [ ! -d "database" ]; then
    echo "Creating database directory..."
    mkdir -p database
fi

if [ ! -f "database/chats.db" ]; then
    echo "Initializing database..."
    ./venv/bin/python3 init_db.py
fi

nohup ./venv/bin/python3 server.py > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PID_FILE"

echo "Server started with PID: $NEW_PID"
echo "Logs are being written to $LOG_FILE"
