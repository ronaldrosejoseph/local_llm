#!/bin/bash

# start.sh - Starts the LLM server in the background
# Works both in dev mode (current directory) and bundled inside a .app

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Detect if we're running from inside a .app bundle
IS_BUNDLED=false
if [[ "$SCRIPT_DIR" == *".app/Contents/Resources/project" ]]; then
    IS_BUNDLED=true
fi

if [ "$IS_BUNDLED" = true ]; then
    DATA_DIR="$HOME/Library/Application Support/Local LLM"
    mkdir -p "$DATA_DIR"
    PROJECT_DIR="$SCRIPT_DIR"
else
    DATA_DIR="$SCRIPT_DIR"
    PROJECT_DIR="$SCRIPT_DIR"
fi

PID_FILE="$DATA_DIR/server.pid"
LOG_FILE="$DATA_DIR/server.log"
STATUS_FILE="$DATA_DIR/.startup_status"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if [[ "$PID" =~ ^[0-9]+$ ]] && ps -p "$PID" > /dev/null; then
        echo "Server is already running (PID: $PID)"
        exit 0
    else
        echo "Stale or invalid PID file found. Removing..."
        rm "$PID_FILE"
    fi
fi

echo "Starting LLM server..."
echo "Checking system environment..." > "$STATUS_FILE"

# 1. Setup Homebrew if missing
if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Installing Homebrew..."
    echo "Installing Homebrew..." > "$STATUS_FILE"
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    if [[ "$(uname -m)" == "arm64" ]]; then
        export PATH="/opt/homebrew/bin:$PATH"
    else
        export PATH="/usr/local/bin:$PATH"
    fi
fi

# 2. Setup Python via Homebrew
if ! brew list python@3 &>/dev/null && ! brew list python3 &>/dev/null && ! brew list python &>/dev/null; then
    echo "Python 3 not found. Installing via Homebrew..."
    echo "Installing Python 3 via Homebrew..." > "$STATUS_FILE"
    HOMEBREW_NO_AUTO_UPDATE=1 brew install python3
else
    if ! "$(brew --prefix)/bin/python3" -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" 2>/dev/null; then
        echo "Installed Python is older than 3.14. Upgrading via Homebrew..."
        echo "Upgrading Python via Homebrew..." > "$STATUS_FILE"
        brew upgrade python3
    fi
fi
PYTHON_CMD="$(brew --prefix)/bin/python3"

echo "Using Python: $("$PYTHON_CMD" --version)"

# 3. Create venv if it doesn't exist (in DATA_DIR for bundled, local for dev)
VENV_DIR="$DATA_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Virtual environment not found. Creating venv..."
    echo "Creating virtual environment..." > "$STATUS_FILE"
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create venv. Please make sure python3 is installed."
        exit 1
    fi
fi

# 4. Install/Update requirements (only when requirements.txt changes)
REQ_HASH_FILE="$DATA_DIR/.requirements.hash"
REQ_FILE="$PROJECT_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    if command -v md5 >/dev/null 2>&1; then
        REQ_HASH=$(md5 -q "$REQ_FILE")
    else
        REQ_HASH=$(md5sum "$REQ_FILE" | awk '{ print $1 }')
    fi

    if [ ! -f "$REQ_HASH_FILE" ] || [ "$REQ_HASH" != "$(cat "$REQ_HASH_FILE")" ]; then
        echo "Requirements changed. Installing/updating..."
        echo "Installing Python packages (this may take a few minutes)..." > "$STATUS_FILE"
        "$VENV_DIR/bin/pip" install -r "$REQ_FILE"
        if [ $? -ne 0 ]; then
            echo "Error: Failed to install requirements."
            exit 1
        fi
        echo "$REQ_HASH" > "$REQ_HASH_FILE"
    else
        echo "Requirements up to date."
    fi
fi

# 5. Initialize database and handle schema updates
DB_DIR="$DATA_DIR/database"
if [ ! -d "$DB_DIR" ]; then
    echo "Creating database directory..."
    mkdir -p "$DB_DIR"
fi

HASH_FILE="$DATA_DIR/.init_db.hash"
INIT_DB_PY="$PROJECT_DIR/init_db.py"
if command -v md5 >/dev/null 2>&1; then
    CURRENT_HASH=$(md5 -q "$INIT_DB_PY")
else
    CURRENT_HASH=$(md5sum "$INIT_DB_PY" | awk '{ print $1 }')
fi

NEED_INIT=false
if [ ! -f "$DB_DIR/chats.db" ]; then
    echo "Database missing. Initializing..."
    NEED_INIT=true
elif [ ! -f "$HASH_FILE" ] || [ "$CURRENT_HASH" != "$(cat "$HASH_FILE")" ]; then
    echo "Changes detected in init_db.py. Running schema updates..."
    NEED_INIT=true
fi

if [ "$NEED_INIT" = true ]; then
    cd "$PROJECT_DIR" && LOCAL_LLM_DATA_DIR="$DATA_DIR" "$VENV_DIR/bin/python3" init_db.py
    echo "$CURRENT_HASH" > "$HASH_FILE"
fi

# 6. Seed config.json from defaults if it doesn't exist in DATA_DIR
if [ ! -f "$DATA_DIR/config.json" ] && [ -f "$PROJECT_DIR/config.json" ]; then
    cp "$PROJECT_DIR/config.json" "$DATA_DIR/config.json"
fi

echo "Starting server and loading model..." > "$STATUS_FILE"
cd "$PROJECT_DIR" && LOCAL_LLM_DATA_DIR="$DATA_DIR" nohup "$VENV_DIR/bin/python3" server.py > "$LOG_FILE" 2>&1 &
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
    if grep -iq "OperationalError: database is locked" "$LOG_FILE" 2>/dev/null; then
        echo ""
        echo "❌ Error: Database is locked by another process."
        echo "   Please run ./stop.sh manually to clear lingering processes."
        exit 1
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

echo ""
if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "⚠️  Server is taking longer than expected to start."
    echo "   Check $LOG_FILE for details. It may still be loading the model."
else
    if grep -iqE "(database.*error|error.*database)" "$LOG_FILE" 2>/dev/null; then
        echo "⚠️  Server started, but database errors were detected in logs."
    else
        echo "✅ Server is ready!"
    fi
    echo "Ready" > "$STATUS_FILE"
fi

echo ""
echo "   🌐  Local:   http://localhost:8000"
echo "   📄  Logs:    tail -f $LOG_FILE"
echo ""
