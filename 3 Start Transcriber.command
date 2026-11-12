#!/bin/bash
set -uo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT_DIR="$APP_DIR/Support Files"
VERSION_FILE="$APP_DIR/VERSION.txt"
VENV_DIR="$SUPPORT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
STANDALONE_PY="$SUPPORT_DIR/.python/bin/python3"
UV="$SUPPORT_DIR/.tools/uv"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export UV_CACHE_DIR="$SUPPORT_DIR/.uv-cache"
export UV_PYTHON_DOWNLOADS=never
export UV_LINK_MODE=copy

VERSION="unknown"
if [[ -f "$VERSION_FILE" ]]; then
    VERSION="$(cat "$VERSION_FILE")"
fi

echo "================================================================"
echo "  Audio Transcriber (v$VERSION)"
echo "================================================================"
echo "App folder: $APP_DIR"
echo

# Self-heal .venv automatically if the user moved the audio-transcriber folder after setup!
if [[ -x "$STANDALONE_PY" && -x "$UV" ]]; then
    NEED_RELINK=0
    if [[ ! -x "$VENV_PY" ]]; then
        NEED_RELINK=1
    elif [[ -f "$VENV_DIR/.installed_app_dir" ]] && [[ "$(cat "$VENV_DIR/.installed_app_dir")" != "$APP_DIR" ]]; then
        NEED_RELINK=1
    fi

    if [[ "$NEED_RELINK" -eq 1 ]]; then
        echo "Detected that this folder was moved since setup — refreshing environment links..."
        if ! "$UV" venv --allow-existing --python "$STANDALONE_PY" "$VENV_DIR"; then
            echo "ERROR: Could not refresh the private Python environment."
            echo "Run 'More Tools/Repair Setup.command', then try again."
            read -r -p "Press Enter to close..."
            exit 1
        fi
        if ! "$UV" pip install --offline --python "$VENV_PY" --only-binary :all: --find-links "$SUPPORT_DIR/wheels" -r "$SUPPORT_DIR/requirements.lock" >/dev/null 2>&1 && \
           ! "$UV" pip install --python "$VENV_PY" --only-binary :all: --find-links "$SUPPORT_DIR/wheels" -r "$SUPPORT_DIR/requirements.lock"; then
            echo "ERROR: Could not restore the required Python packages."
            echo "Run 'More Tools/Repair Setup.command', then try again."
            read -r -p "Press Enter to close..."
            exit 1
        fi
        printf '%s\n' "$APP_DIR" > "$VENV_DIR/.installed_app_dir"
        echo "Environment refreshed!"
        echo
    fi
fi

if [[ ! -x "$VENV_PY" ]]; then
    echo "Setup has not been run yet."
    echo "Please double-click '1 Setup.command' first."
    echo
    read -r -p "Press Enter to close..."
    exit 1
fi

# Catch Ctrl+C in this wrapper so Python shuts down cleanly AND we drop into the
# live restart prompt instead of letting Terminal close with [Process completed].
INTERRUPTED=0
trap 'INTERRUPTED=1' INT

/usr/bin/nice -n 10 "$VENV_PY" -u "$SUPPORT_DIR/transcriber.py"
STATUS=$?

trap - INT

# Exit code 2 = another Terminal window is already running Audio Transcriber.
if [[ "$STATUS" -eq 2 ]]; then
    echo
    read -r -p "Press Enter to close this extra window..."
    exit 0
fi

if [[ "$STATUS" -ne 0 && "$STATUS" -ne 130 && "$INTERRUPTED" -eq 0 ]]; then
    echo
    echo "Audio Transcriber stopped because of an error (code $STATUS)."
    echo "Tip: Try 'More Tools/Check Setup.command', or check:"
    echo "  $SUPPORT_DIR/logs/transcriber.log"
fi

# If we are ALREADY inside our interactive restart shell, return straight to it
# so repeated Ctrl+C -> Up-Arrow -> Enter cycles never stack nested shells!
if [[ "${AT_RESTART_SHELL:-0}" == "1" ]]; then
    echo
    echo "================================================================"
    echo "Audio Transcriber is stopped."
    echo "  - To restart : press Up-Arrow, then Enter  (or type: restart)"
    echo "  - To quit    : close this Terminal window"
    echo "================================================================"
    echo
    exit 0
fi

# First stop after opening from Finder or Login Auto-Start: replace this script
# with our clean interactive zsh where Up-Arrow + Enter immediately restarts!
export AT_START_CMD="$APP_DIR/3 Start Transcriber.command"
export ZDOTDIR="$SUPPORT_DIR/.shell"
exec /bin/zsh -i
