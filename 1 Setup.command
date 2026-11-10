#!/bin/bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT_DIR="$APP_DIR/Support Files"
VERSION_FILE="$APP_DIR/VERSION.txt"
TOKEN_FILE="$APP_DIR/My Hugging Face Token.txt"
TOOLS_DIR="$SUPPORT_DIR/.tools"
UV="$TOOLS_DIR/uv"
STANDALONE_PY_DIR="$SUPPORT_DIR/.python"
STANDALONE_PY="$STANDALONE_PY_DIR/bin/python3"
VENV_DIR="$SUPPORT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

# Pinned official Astral release artifacts & SHA-256 checksums (Apple Silicon macOS)
UV_URL="https://github.com/astral-sh/uv/releases/download/0.13.6/uv-aarch64-apple-darwin.tar.gz"
UV_SHA256="60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472"

PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-aarch64-apple-darwin-install_only_stripped.tar.gz"
PY_SHA256="353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export UV_CACHE_DIR="$SUPPORT_DIR/.uv-cache"
export UV_PYTHON_DOWNLOADS=never
export UV_LINK_MODE=copy

VERSION="unknown"
if [[ -f "$VERSION_FILE" ]]; then
    VERSION="$(cat "$VERSION_FILE")"
fi

finish() {
    if [[ "${AT_SKIP_FINISH_PROMPT:-0}" == "1" ]]; then
        return 0
    fi
    echo
    read -r -p "Press Enter to close..." || true
}
trap finish EXIT

# If launched via a pipe (`curl ... | bash`), reconnect stdin to the Terminal keyboard.
if [[ ! -t 0 && "${AT_NONINTERACTIVE:-0}" != "1" && -r /dev/tty ]]; then
    exec </dev/tty
fi

echo "================================================================"
echo "  Audio Transcriber — One-Time Setup (v$VERSION)"
echo "================================================================"
echo "App folder: $APP_DIR"
echo "No administrator password is needed."
echo

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: This .command file is for macOS."
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
    echo "ERROR: This Mac uses an older Intel processor ($ARCH)."
    echo "Audio Transcriber is built for Apple Silicon Macs (M1, M2, M3, M4 or newer)."
    exit 1
fi

# Clear macOS download quarantine on all scripts and bundled files inside this folder
# so the user never sees another Gatekeeper prompt after opening 1 Setup.command.
if [[ -x /usr/bin/xattr ]]; then
    /usr/bin/xattr -dr com.apple.quarantine "$APP_DIR" >/dev/null 2>&1 || true
fi

FREE_KB="$(/bin/df -Pk "$APP_DIR" | /usr/bin/awk 'NR==2 {print $4}')"
MIN_KB=$((4 * 1024 * 1024))
RECOMMENDED_KB=$((8 * 1024 * 1024))
if [[ -n "$FREE_KB" && "$FREE_KB" -lt "$MIN_KB" ]]; then
    echo "ERROR: Less than 4 GB of free disk space is available."
    echo "Please free up at least 8 GB of space, then run 1 Setup.command again."
    exit 1
elif [[ -n "$FREE_KB" && "$FREE_KB" -lt "$RECOMMENDED_KB" ]]; then
    echo "Note: Less than the recommended 8 GB of free disk space is available."
    echo "Setup should still work, though extra space helps with large audio files."
    echo
fi

mkdir -p \
    "$TOOLS_DIR" \
    "$APP_DIR/Audio to Transcribe" \
    "$APP_DIR/Finished Transcripts" \
    "$APP_DIR/More Tools" \
    "$SUPPORT_DIR/logs" \
    "$SUPPORT_DIR/models"

chmod +x \
    "$APP_DIR/1 Setup.command" \
    "$APP_DIR/2 Choose Folders.command" \
    "$APP_DIR/3 Start Transcriber.command" \
    "$APP_DIR/Auto Start at Login (Optional).command" \
    "$APP_DIR/More Tools/Check Setup.command" \
    "$APP_DIR/More Tools/Download Models.command" \
    "$APP_DIR/More Tools/Repair Setup.command" \
    "$APP_DIR/More Tools/Make Support Zip.command" 2>/dev/null || true

verify_sha256() {
    local file="$1"
    local expected="$2"
    local actual
    actual="$(/usr/bin/shasum -a 256 "$file" | /usr/bin/awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: Download checksum mismatch for $(basename "$file")."
        echo "Expected: $expected"
        echo "Actual:   $actual"
        return 1
    fi
    return 0
}

# Step 1: Install private uv binary (no admin rights, no global PATH edits)
if [[ ! -x "$UV" ]]; then
    echo "[Step 1/4] Downloading private package installer (uv)..."
    TMP_UV="$(mktemp -d "$SUPPORT_DIR/.tmp-uv.XXXXXX")"
    /usr/bin/curl -fL --retry 3 --connect-timeout 20 -o "$TMP_UV/uv.tar.gz" "$UV_URL"
    if ! verify_sha256 "$TMP_UV/uv.tar.gz" "$UV_SHA256"; then
        rm -rf "$TMP_UV"
        exit 1
    fi
    /usr/bin/tar -xzf "$TMP_UV/uv.tar.gz" -C "$TMP_UV"
    FOUND_UV="$(/usr/bin/find "$TMP_UV" -name uv -type f | /usr/bin/head -n 1)"
    if [[ -z "$FOUND_UV" ]]; then
        rm -rf "$TMP_UV"
        echo "ERROR: Could not unpack uv installer."
        exit 1
    fi
    chmod +x "$FOUND_UV"
    /usr/bin/xattr -dr com.apple.quarantine "$FOUND_UV" >/dev/null 2>&1 || true
    mv -f "$FOUND_UV" "$UV"
    rm -rf "$TMP_UV"
else
    echo "[Step 1/4] Private package installer (uv) is already ready."
fi

# Step 2: Install standalone Python 3.12 directly (zero Xcode Command Line Tools, zero admin password!)
if [[ ! -x "$STANDALONE_PY" ]]; then
    echo
    echo "[Step 2/4] Downloading private Python 3.12 (~18 MB, no admin password required)..."
    TMP_PY="$(mktemp -d "$SUPPORT_DIR/.tmp-py.XXXXXX")"
    /usr/bin/curl -fL --retry 3 --connect-timeout 20 -o "$TMP_PY/python.tar.gz" "$PY_URL"
    if ! verify_sha256 "$TMP_PY/python.tar.gz" "$PY_SHA256"; then
        rm -rf "$TMP_PY"
        exit 1
    fi
    /usr/bin/tar -xzf "$TMP_PY/python.tar.gz" -C "$TMP_PY"
    if [[ ! -x "$TMP_PY/python/bin/python3" ]]; then
        rm -rf "$TMP_PY"
        echo "ERROR: Unpacked Python 3.12 was missing bin/python3."
        exit 1
    fi
    /usr/bin/xattr -dr com.apple.quarantine "$TMP_PY/python" >/dev/null 2>&1 || true
    if ! "$TMP_PY/python/bin/python3" -c "import sys; assert sys.version_info[:2] == (3, 12)"; then
        rm -rf "$TMP_PY"
        echo "ERROR: Downloaded Python 3.12 failed self-test."
        exit 1
    fi
    rm -rf "$STANDALONE_PY_DIR"
    mv "$TMP_PY/python" "$STANDALONE_PY_DIR"
    rm -rf "$TMP_PY"
else
    echo "[Step 2/4] Private Python 3.12 is already installed."
fi

# Step 3: Create/verify private .venv and install prebuilt wheels (--only-binary :all:)
NEED_VENV_CREATE=0
if [[ ! -x "$VENV_PY" ]]; then
    NEED_VENV_CREATE=1
elif [[ -f "$VENV_DIR/.installed_app_dir" ]] && [[ "$(cat "$VENV_DIR/.installed_app_dir")" != "$APP_DIR" ]]; then
    echo "Folder location changed since last setup; refreshing private environment..."
    NEED_VENV_CREATE=1
fi

if [[ "$NEED_VENV_CREATE" -eq 1 ]]; then
    echo
    echo "[Step 3/4] Creating private Python environment..."
    "$UV" venv --allow-existing --python "$STANDALONE_PY" "$VENV_DIR"
    printf '%s\n' "$APP_DIR" > "$VENV_DIR/.installed_app_dir"
fi

echo
echo "[Step 3/4] Installing transcription & speaker-label packages (may take 2-5 minutes the first time)..."
"$UV" pip install \
    --python "$VENV_PY" \
    --only-binary :all: \
    --find-links "$SUPPORT_DIR/wheels" \
    -r "$SUPPORT_DIR/requirements.lock"
printf '%s\n' "$APP_DIR" > "$VENV_DIR/.installed_app_dir"

if [[ ! -f "$TOKEN_FILE" ]]; then
    printf 'YOUR_TOKEN_HERE\n' > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE" 2>/dev/null || true
fi

# Step 4: Check Hugging Face token & download AI models
CURRENT_TOKEN="$("$VENV_PY" -c '
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[1])))
import transcriber
cfg = transcriber.load_config()
tok, _ = transcriber.read_hugging_face_token(cfg)
print(tok)
' "$SUPPORT_DIR" 2>/dev/null || true)"

echo
echo "[Step 4/4] Checking Hugging Face speaker-label token & AI models..."
if [[ -z "$CURRENT_TOKEN" || "$CURRENT_TOKEN" == "YOUR_TOKEN_HERE" || "$CURRENT_TOKEN" == "hf_your_actual_token_here" || "$CURRENT_TOKEN" == "SAVED_IN_KEYCHAIN" ]]; then
    echo
    echo "----------------------------------------------------------------"
    echo "Almost done! Speaker labels require a free Hugging Face token:"
    echo "  1. Create a free account at: https://huggingface.co/join"
    echo "  2. Open BOTH links below while logged in and click 'Agree':"
    echo "       - https://huggingface.co/pyannote/segmentation-3.0"
    echo "       - https://huggingface.co/pyannote/speaker-diarization-3.1"
    echo "  3. Create a token at: https://huggingface.co/settings/tokens"
    echo "     (Click 'Create new token', select 'Read' at the top, name it"
    echo "      'audio-transcriber', and copy your hf_... token)"
    echo "----------------------------------------------------------------"
    echo
    echo "You can paste your hf_... token right here (characters are hidden for privacy),"
    echo "OR press Enter to skip for now and paste it into 'My Hugging Face Token.txt' later."
    read -r -s -p "Paste hf_ token (or press Enter to skip for now): " PASTED_TOKEN
    echo
    PASTED_TOKEN="${PASTED_TOKEN#"${PASTED_TOKEN%%[![:space:]]*}"}"
    PASTED_TOKEN="${PASTED_TOKEN%"${PASTED_TOKEN##*[![:space:]]}"}"
    if [[ -n "$PASTED_TOKEN" ]]; then
        printf '%s\n' "$PASTED_TOKEN" | "$VENV_PY" -u "$SUPPORT_DIR/transcriber.py" --save-token-stdin
        CURRENT_TOKEN="$PASTED_TOKEN"
    fi
fi

if [[ -z "$CURRENT_TOKEN" || "$CURRENT_TOKEN" == "YOUR_TOKEN_HERE" || "$CURRENT_TOKEN" == "hf_your_actual_token_here" || "$CURRENT_TOKEN" == "SAVED_IN_KEYCHAIN" ]]; then
    echo
    echo "================================================================"
    echo "Software install is complete! One step left before transcribing:"
    echo "  1. Paste your hf_... token into 'My Hugging Face Token.txt' and save."
    echo "  2. Run '1 Setup.command' again so it can verify your token and"
    echo "     finish downloading the AI models."
    echo "================================================================"
    exit 0
fi

set +e
"$VENV_PY" -u "$SUPPORT_DIR/transcriber.py" --download-models
MODEL_STATUS=$?
set -e

echo
if [[ "$MODEL_STATUS" -eq 0 ]]; then
    if [[ "${AT_NO_FINDER_OPEN:-0}" != "1" ]]; then
        /usr/bin/open "$APP_DIR" >/dev/null 2>&1 || true
    fi
    echo "================================================================"
    echo "ALL SET! Audio Transcriber is completely ready."
    echo "================================================================"
    echo "Your Audio Transcriber folder is open in Finder ($APP_DIR)."
    echo "Next steps (just double-click in Finder):"
    echo "  - Pick your Watch & Transcript folders : 2 Choose Folders.command"
    echo "  - Start watching for audio files       : 3 Start Transcriber.command"
    echo "  - Open automatically when you log in   : Auto Start at Login (Optional).command"
    echo "================================================================"
else
    echo "================================================================"
    echo "Software is installed, but token or model check needs attention."
    echo "Read the FAIL message above, fix it, and run '1 Setup.command'"
    echo "(or 'More Tools/Check Setup.command') again."
    echo "================================================================"
    exit "$MODEL_STATUS"
fi
