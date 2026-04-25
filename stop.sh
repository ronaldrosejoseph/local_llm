#!/bin/bash

# stop.sh - Stops the LLM server

PID_FILE="server.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    echo "Stopping LLM server (PID: $PID)..."
    kill "$PID" 2>/dev/null
    # Wait for the process to exit
    MAX_RETRIES=5
    RETRY_COUNT=0
    while ps -p "$PID" > /dev/null && [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        sleep 1
        RETRY_COUNT=$((RETRY_COUNT + 1))
    done
    if ps -p "$PID" > /dev/null; then
        echo "Server did not stop gracefully. Force killing (PID: $PID)..."
        kill -9 "$PID" 2>/dev/null
    fi
    rm "$PID_FILE"
    rm -f ".server_lifecycle"
    echo "Server stopped."
else
    echo "Server is not running (no $PID_FILE found)."
fi
