#!/bin/bash

# stop.sh - Stops the LLM server

PID_FILE="server.pid"

# 1. Try to stop via PID file first
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    # Validate that PID is a number before using it
    if [[ "$PID" =~ ^[0-9]+$ ]]; then
        echo "Stopping LLM server (PID: $PID)..."
        kill "$PID" 2>/dev/null
        
        # Wait up to 5 seconds for it to die naturally
        for i in {1..5}; do
            if ! ps -p "$PID" > /dev/null; then break; fi
            echo "Waiting for server to stop..."
            sleep 1
        done
        
        # Force kill if still alive
        if ps -p "$PID" > /dev/null; then
            echo "Server did not stop gracefully. Force killing (PID: $PID)..."
            kill -9 "$PID" 2>/dev/null
        fi
    else
        echo "Invalid PID in $PID_FILE: '$PID'. Skipping."
    fi
    rm "$PID_FILE"
fi

# 2. Aggressive Cleanup (The "Nuke" option)
# Find any process listening on port 8000 and kill it
PORT_PIDS=$(lsof -t -i:8000)
if [ -n "$PORT_PIDS" ]; then
    echo "Cleaning up lingering processes on port 8000: $PORT_PIDS"
    # Kill each PID found on that port
    for p in $PORT_PIDS; do
        kill -9 "$p" 2>/dev/null
    done
fi

# 3. Final cleanup of any stray server processes by name
STRAY_PIDS=$(pgrep -f "python3 server.py")
if [ -n "$STRAY_PIDS" ]; then
    echo "Cleaning up stray server processes: $STRAY_PIDS"
    for p in $STRAY_PIDS; do
        kill -9 "$p" 2>/dev/null
    done
fi

rm -f ".server_lifecycle"
echo "✅ Environment is clean."
