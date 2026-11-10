#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPPORT_DIR="$APP_DIR/Support Files"

finish() {
    echo
    read -r -p "Press Enter to close..."
}
trap finish EXIT

PYTHON=""
if [[ -x "$SUPPORT_DIR/.venv/bin/python" ]]; then
    PYTHON="$SUPPORT_DIR/.venv/bin/python"
elif [[ -x "$SUPPORT_DIR/.python/bin/python3" ]]; then
    PYTHON="$SUPPORT_DIR/.python/bin/python3"
fi

if [[ -n "$PYTHON" ]]; then
    "$PYTHON" -u "$SUPPORT_DIR/transcriber.py" --make-support-zip
    exit $?
fi

# Fallback if 1 Setup.command failed before Python finished unpacking:
OUT_ZIP="$APP_DIR/Audio Transcriber Support Info.zip"
TMP_DIR="$(mktemp -d /tmp/audio-transcriber-support.XXXXXX)"
cleanup() { rm -rf "$TMP_DIR"; }
trap 'cleanup; finish' EXIT

mkdir -p "$TMP_DIR/support-info"
{
    echo "Audio Transcriber Pre-Python Support Info"
    echo "Created: $(date)"
    if [[ -f "$APP_DIR/VERSION.txt" ]]; then
        echo "Version: $(cat "$APP_DIR/VERSION.txt")"
    fi
    echo
    /usr/bin/sw_vers 2>/dev/null || true
    echo
    /bin/df -h "$APP_DIR" 2>/dev/null || true
} > "$TMP_DIR/support-info/system.txt"

rm -f "$OUT_ZIP"
(cd "$TMP_DIR" && /usr/bin/zip -r "$OUT_ZIP" support-info >/dev/null)
echo "Created: $OUT_ZIP"
