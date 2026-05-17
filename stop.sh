#!/bin/bash

# stop.sh - Stops the LLM server

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Detect if we're running from inside a .app bundle
if [[ "$SCRIPT_DIR" == *".app/Contents/Resources/project" ]]; then
    DATA_DIR="$HOME/Library/Application Support/Local LLM"
else
    DATA_DIR="$SCRIPT_DIR"
fi

PID_FILE="$DATA_DIR/server.pid"
SERVER_RUNNING=false

# Check if server is running BEFORE we start killing anything
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if [[ "$PID" =~ ^[0-9]+$ ]] && ps -p "$PID" > /dev/null 2>&1; then 
        SERVER_RUNNING=true
    fi
fi
if [ "$SERVER_RUNNING" = false ]; then
    if lsof -t -i:8000 > /dev/null 2>&1; then SERVER_RUNNING=true; fi
fi
if [ "$SERVER_RUNNING" = false ]; then
    if pgrep -f "python3 server.py" > /dev/null 2>&1; then SERVER_RUNNING=true; fi
fi

# 1. Try to stop via PID file first
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if [[ "$PID" =~ ^[0-9]+$ ]]; then
        echo "Stopping LLM server (PID: $PID)..."
        kill "$PID" 2>/dev/null

        for i in {1..8}; do
            if ! ps -p "$PID" > /dev/null; then break; fi
            echo "Waiting for server to stop..."
            sleep 1
        done
        
        if ps -p "$PID" > /dev/null; then
            echo "Server did not stop gracefully. Force killing (PID: $PID)..."
            kill -9 "$PID" 2>/dev/null
        fi
    fi
    rm "$PID_FILE"
fi

# 2. Aggressive Cleanup
PORT_PIDS=$(lsof -t -i:8000)
if [ -n "$PORT_PIDS" ]; then
    echo "Cleaning up lingering processes on port 8000: $PORT_PIDS"
    for p in $PORT_PIDS; do
        kill -9 "$p" 2>/dev/null
    done
fi

# 3. Final cleanup by name
STRAY_PIDS=$(pgrep -f "python3 server.py")
if [ -n "$STRAY_PIDS" ]; then
    echo "Cleaning up stray server processes: $STRAY_PIDS"
    for p in $STRAY_PIDS; do
        kill -9 "$p" 2>/dev/null
    done
fi

# 4. Clean up model worker child processes
WORKER_PIDS=$(pgrep -f "worker.py" 2>/dev/null)
if [ -n "$WORKER_PIDS" ]; then
    echo "Cleaning up worker processes: $WORKER_PIDS"
    for p in $WORKER_PIDS; do
        kill "$p" 2>/dev/null
    done
    sleep 1
    for p in $WORKER_PIDS; do
        if ps -p "$p" > /dev/null 2>&1; then
            kill -9 "$p" 2>/dev/null
        fi
    done
fi
rm -f "worker.pid"

# 5. Clean up title worker processes
TITLE_PIDS=$(pgrep -f "title_worker.py" 2>/dev/null)
if [ -n "$TITLE_PIDS" ]; then
    echo "Cleaning up title worker processes: $TITLE_PIDS"
    for p in $TITLE_PIDS; do
        kill -9 "$p" 2>/dev/null
    done
fi

if [ "$SERVER_RUNNING" = true ]; then
    rm -f ".server_lifecycle"
    echo "✅ Server stopped and environment is clean."
else
    echo "⚠️ Server was not running. Preserving .server_lifecycle for crash detection."
fi
