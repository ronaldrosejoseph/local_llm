#!/bin/bash

# start.sh - Starts the LLM server in the background

PID_FILE="server.pid"
LOG_FILE="server.log"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    # Validate that PID is a number before using it
    if [[ "$PID" =~ ^[0-9]+$ ]] && ps -p "$PID" > /dev/null; then
        echo "Server is already running (PID: $PID)"
        exit 0
    else
        echo "Stale or invalid PID file found. Removing..."
        rm "$PID_FILE"
    fi
fi

echo "Starting LLM server..."

# 1. Setup Homebrew if missing
if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Installing Homebrew..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add Homebrew to PATH for the current session without using eval
    if [[ "$(uname -m)" == "arm64" ]]; then
        export PATH="/opt/homebrew/bin:$PATH"
    else
        export PATH="/usr/local/bin:$PATH"
    fi
fi

# 2. Setup Python via Homebrew
if ! brew list python@3 &>/dev/null && ! brew list python3 &>/dev/null && ! brew list python &>/dev/null; then
    echo "Python 3 not found. Installing via Homebrew..."
    # Disable auto-update just for this install to speed it up
    HOMEBREW_NO_AUTO_UPDATE=1 brew install python3
else
    # Swiftly check if the installed brew python is older than 3.14 without hitting the network
    # Swiftly check if the installed brew python is older than 3.14 without hitting the network
    if ! "$(brew --prefix)/bin/python3" -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" 2>/dev/null; then
        echo "Installed Python is older than 3.14. Upgrading via Homebrew..."
        brew upgrade python3
    fi
fi
PYTHON_CMD="$(brew --prefix)/bin/python3"

echo "Using Python: $("$PYTHON_CMD" --version)"

# 3. Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating venv..."
    "$PYTHON_CMD" -m venv venv
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

# 3. Initialize database and handle schema updates
if [ ! -d "database" ]; then
    echo "Creating database directory..."
    mkdir -p database
fi

HASH_FILE=".init_db.hash"
# Use md5 on macOS to check if init_db.py has changed
if command -v md5 >/dev/null 2>&1; then
    CURRENT_HASH=$(md5 -q init_db.py)
else
    # Fallback for systems with md5sum
    CURRENT_HASH=$(md5sum init_db.py | awk '{ print $1 }')
fi

NEED_INIT=false
if [ ! -f "database/chats.db" ]; then
    echo "Database missing. Initializing..."
    NEED_INIT=true
elif [ ! -f "$HASH_FILE" ] || [ "$CURRENT_HASH" != "$(cat "$HASH_FILE")" ]; then
    echo "Changes detected in init_db.py. Running schema updates..."
    NEED_INIT=true
fi

if [ "$NEED_INIT" = true ]; then
    ./venv/bin/python3 init_db.py
    echo "$CURRENT_HASH" > "$HASH_FILE"
fi

nohup ./venv/bin/python3 server.py > "$LOG_FILE" 2>&1 &
NEW_PID="$!"
echo "$NEW_PID" > "$PID_FILE"

echo "Server started with PID: $NEW_PID"
echo "Logs are being written to $LOG_FILE"
echo ""
echo "⏳ Waiting for server to be ready (this may take a moment while the model loads)..."

# Poll the log file until uvicorn reports startup complete, or timeout after 120s
TIMEOUT=120
ELAPSED=0
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    if ! ps -p "$NEW_PID" > /dev/null 2>&1; then
        echo ""
        echo "❌ Server process exited unexpectedly. Check $LOG_FILE for details."
        exit 1
    fi
    if grep -q "Application startup complete" "$LOG_FILE" 2>/dev/null; then
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

echo ""
if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "⚠️  Server is taking longer than expected to start."
    echo "   Check $LOG_FILE for details. It may still be loading the model."
else
    echo "✅ Server is ready!"
fi

echo ""
echo "   🌐  Local:   http://localhost:8000"
echo "   📄  Logs:    tail -f $LOG_FILE"
echo ""
