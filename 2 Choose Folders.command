#!/bin/bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
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

if [[ -z "$PYTHON" ]]; then
    echo "=== Choose Audio & Transcript Folders ==="
    echo
    echo "Please double-click '1 Setup.command' once first to install the private"
    echo "Python runtime, then run '2 Choose Folders.command' anytime."
    exit 1
fi

"$PYTHON" -u "$SUPPORT_DIR/choose_folders.py"
