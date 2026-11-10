#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPPORT_DIR="$APP_DIR/Support Files"
VENV_PY="$SUPPORT_DIR/.venv/bin/python"

finish() {
    echo
    read -r -p "Press Enter to close..."
}
trap finish EXIT

echo "=== Audio Transcriber Setup Check ==="
echo "Folder: $APP_DIR"
echo

if [[ ! -x "$VENV_PY" ]]; then
    echo "Private Python environment is not installed yet."
    echo "Please double-click '1 Setup.command' first."
    exit 1
fi

set +e
"$VENV_PY" -u "$SUPPORT_DIR/transcriber.py" --check
STATUS=$?
set -e

echo
if [[ "$STATUS" -eq 0 ]]; then
    echo "Check passed! You can double-click '3 Start Transcriber.command'."
else
    echo "Check did not pass yet. Read the FAIL message(s) above, fix them, and run this check again."
fi
exit "$STATUS"
