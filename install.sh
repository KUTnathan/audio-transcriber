#!/bin/bash
set -euo pipefail

# ============================================================================
# Audio Transcriber — One-Line macOS Installer & Updater
# Usage in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/KUTnathan/audio-transcriber/main/install.sh | bash
# ============================================================================

REPO_OWNER="${AT_REPO_OWNER:-KUTnathan}"
REPO_NAME="${AT_REPO_NAME:-audio-transcriber}"
REPO_BRANCH="${AT_REPO_BRANCH:-main}"
TARBALL_URL="${AT_TARBALL_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${REPO_BRANCH}.tar.gz}"
INSTALL_DIR="${AUDIO_TRANSCRIBER_DIR:-$HOME/audio-transcriber}"

echo "================================================================"
echo "  Audio Transcriber — Mac Installer"
echo "================================================================"
echo "Destination folder: $INSTALL_DIR"
echo "No administrator password is required."
echo

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: This installer is for macOS."
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
    if [[ "$ARCH" == "x86_64" && "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" == "1" ]]; then
        echo "ERROR: Terminal is running in Rosetta mode on an Apple Silicon Mac."
        echo "Turn off 'Open using Rosetta' for Terminal, reopen Terminal, and try again."
        exit 1
    fi
    echo "ERROR: This Mac uses an older Intel processor ($ARCH)."
    echo "Audio Transcriber is built for Apple Silicon Macs (M1, M2, M3, M4 or newer)."
    exit 1
fi

# Never replace program files while a transcription is running.
EXISTING_PYTHON="$INSTALL_DIR/Support Files/.venv/bin/python"
EXISTING_APP="$INSTALL_DIR/Support Files/transcriber.py"
if [[ -x "$EXISTING_PYTHON" && -f "$EXISTING_APP" ]] && \
   "$EXISTING_PYTHON" "$EXISTING_APP" --is-running >/dev/null 2>&1; then
    echo "ERROR: Audio Transcriber is currently running."
    echo "Click its Terminal window, press Control+C, then run this installer again."
    exit 1
fi

TMP_STAGE="$(mktemp -d /tmp/audio-transcriber-install.XXXXXX)"
cleanup() {
    rm -rf "$TMP_STAGE"
}
trap cleanup EXIT

echo "Downloading Audio Transcriber from GitHub..."
/usr/bin/curl -fL --retry 3 --connect-timeout 20 -o "$TMP_STAGE/repo.tar.gz" "$TARBALL_URL"
/usr/bin/tar -xzf "$TMP_STAGE/repo.tar.gz" -C "$TMP_STAGE"

EXTRACTED_DIR="$(/usr/bin/find "$TMP_STAGE" -mindepth 1 -maxdepth 1 -type d | /usr/bin/head -n 1)"
if [[ -z "$EXTRACTED_DIR" || ! -f "$EXTRACTED_DIR/1 Setup.command" ]]; then
    echo "ERROR: Downloaded archive did not contain '1 Setup.command'."
    exit 1
fi

# Preserve user's existing Settings.ini and My Hugging Face Token.txt if updating an existing install!
HAS_EXISTING_SETTINGS=0
HAS_EXISTING_TOKEN=0
if [[ -f "$INSTALL_DIR/Settings.ini" ]]; then
    cp "$INSTALL_DIR/Settings.ini" "$TMP_STAGE/Settings.ini.bak"
    HAS_EXISTING_SETTINGS=1
fi
if [[ -f "$INSTALL_DIR/My Hugging Face Token.txt" ]]; then
    cp "$INSTALL_DIR/My Hugging Face Token.txt" "$TMP_STAGE/token.txt.bak"
    HAS_EXISTING_TOKEN=1
fi

mkdir -p "$INSTALL_DIR"
/usr/bin/tar -cf - -C "$EXTRACTED_DIR" . | /usr/bin/tar -xf - -C "$INSTALL_DIR"

# README.md is useful on GitHub; Finder users get the cleaner 0 Read Me First.txt.
rm -f "$INSTALL_DIR/README.md"

if [[ "$HAS_EXISTING_SETTINGS" -eq 1 ]]; then
    cp "$TMP_STAGE/Settings.ini.bak" "$INSTALL_DIR/Settings.ini"
fi
if [[ "$HAS_EXISTING_TOKEN" -eq 1 ]]; then
    cp "$TMP_STAGE/token.txt.bak" "$INSTALL_DIR/My Hugging Face Token.txt"
    chmod 600 "$INSTALL_DIR/My Hugging Face Token.txt" 2>/dev/null || true
fi

# Ensure all launcher buttons are executable and completely free of macOS quarantine flags.
chmod +x \
    "$INSTALL_DIR/install.sh" \
    "$INSTALL_DIR/1 Setup.command" \
    "$INSTALL_DIR/2 Choose Folders.command" \
    "$INSTALL_DIR/3 Start Transcriber.command" \
    "$INSTALL_DIR/Auto Start at Login (Optional).command" \
    "$INSTALL_DIR/More Tools/"*.command 2>/dev/null || true

if [[ -x /usr/bin/xattr ]]; then
    /usr/bin/xattr -cr "$INSTALL_DIR" >/dev/null 2>&1 || true
fi

# Pop open the folder in Finder so the user can see the buttons right away.
if [[ "${AT_NO_FINDER_OPEN:-0}" != "1" ]]; then
    /usr/bin/open "$INSTALL_DIR" >/dev/null 2>&1 || true
fi

echo "Downloaded to: $INSTALL_DIR"
echo "Starting one-time setup..."
echo

# `exec` does not run the EXIT trap, so remove downloaded staging files first.
cleanup
trap - EXIT

# Reconnect stdin to the controlling Terminal keyboard when invoked via `curl ... | bash`
if [[ "${AT_NONINTERACTIVE:-0}" != "1" && -r /dev/tty ]]; then
    exec /bin/bash "$INSTALL_DIR/1 Setup.command" </dev/tty
else
    exec /bin/bash "$INSTALL_DIR/1 Setup.command"
fi
