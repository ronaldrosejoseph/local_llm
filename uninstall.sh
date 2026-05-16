#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
HF_CACHE="$HOME/.cache/huggingface/hub"

echo "⚠️  This will remove the LLM server and optionally the HuggingFace model cache."
echo "    Project: $PROJECT_DIR"
echo "    HF cache: $HF_CACHE"
echo ""
read -rp "Delete HuggingFace cache as well? (Y/n): " DELETE_CACHE
DELETE_CACHE="${DELETE_CACHE:-Y}"
read -rp "Type 'yes' to confirm uninstall: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Uninstall cancelled."
    exit 1
fi

# 1. Stop the server
echo ""
echo "Stopping server..."
if [ -f "$PROJECT_DIR/stop.sh" ]; then
    cd "$PROJECT_DIR" && bash stop.sh
else
    # Manual cleanup if stop.sh is missing
    for pid in $(pgrep -f "python3 server.py" 2>/dev/null); do kill -9 "$pid" 2>/dev/null; done
    for pid in $(pgrep -f "worker.py" 2>/dev/null); do kill -9 "$pid" 2>/dev/null; done
    for pid in $(lsof -t -i:8000 2>/dev/null); do kill -9 "$pid" 2>/dev/null; done
fi
sleep 1

# 2. Remove HuggingFace model cache (unless user opted out)
if [[ "$DELETE_CACHE" =~ ^[Yy]$ ]]; then
    echo "Removing HuggingFace cache..."
    rm -rf "$HF_CACHE" 2>/dev/null && echo "  Removed $HF_CACHE" || echo "  (nothing to remove)"
else
    echo "Keeping HuggingFace cache."
fi

# 3. Remove the project directory (including this script)
echo "Removing project directory..."
rm -rf "$PROJECT_DIR"

echo ""
echo "✅ Uninstall complete."
