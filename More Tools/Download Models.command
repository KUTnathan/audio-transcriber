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

echo "=== Audio Transcriber Model Download / Verification ==="
echo "This checks and downloads the speech, alignment, and speaker-label AI models."
echo "The first time can take several minutes on Wi-Fi."
echo

if [[ ! -x "$VENV_PY" ]]; then
    echo "Private Python environment is not installed yet."
    echo "Please double-click '1 Setup.command' first."
    exit 1
fi

set +e
"$VENV_PY" -u "$SUPPORT_DIR/transcriber.py" --download-models
STATUS=$?
set -e

echo
if [[ "$STATUS" -eq 0 ]]; then
    echo "Models are ready! You can double-click '3 Start Transcriber.command'."
else
    echo "Model download/check failed. Read the FAIL message above."
fi
exit "$STATUS"
