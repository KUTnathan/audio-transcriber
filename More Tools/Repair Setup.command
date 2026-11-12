#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPPORT_DIR="$APP_DIR/Support Files"
PYTHON=""
if [[ -x "$SUPPORT_DIR/.venv/bin/python" ]]; then
    PYTHON="$SUPPORT_DIR/.venv/bin/python"
elif [[ -x "$SUPPORT_DIR/.python/bin/python3" ]]; then
    PYTHON="$SUPPORT_DIR/.python/bin/python3"
fi

echo "=== Audio Transcriber — Repair Setup ==="
echo

# Never delete .venv while an active transcription is running!
if [[ -n "$PYTHON" ]] && "$PYTHON" "$SUPPORT_DIR/transcriber.py" --is-running >/dev/null 2>&1; then
    echo "Audio Transcriber is currently running in another Terminal window."
    echo "Please click that window and press Control+C to stop it before running Repair Setup."
    echo
    read -r -p "Press Enter to close..."
    exit 1
fi

echo "This rebuilds the private Python setup inside Support Files."
echo "It does NOT delete your audio files, transcripts, Hugging Face token, or downloaded AI models."
echo
read -r -p "Type REPAIR and press Enter to continue: " CONFIRM
CONFIRM_CLEAN="$(printf '%s' "${CONFIRM:-}" | /usr/bin/tr '[:lower:]' '[:upper:]' | /usr/bin/tr -d '[:space:]')"
if [[ "$CONFIRM_CLEAN" != "REPAIR" ]]; then
    echo "Repair canceled."
    read -r -p "Press Enter to close..."
    exit 0
fi

rm -rf "$SUPPORT_DIR/.venv" "$SUPPORT_DIR/.python"
exec "$APP_DIR/1 Setup.command"
