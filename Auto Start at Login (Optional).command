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
    echo "=== Auto Start at Login (Optional) ==="
    echo
    echo "Please finish '1 Setup.command' first before turning on Auto Start."
    exit 1
fi

echo "================================================================"
echo "  Auto Start at Login (Optional)"
echo "================================================================"
echo
echo "When ON, Audio Transcriber opens automatically in a visible Terminal"
echo "window whenever you log into your Mac — just like starting it manually,"
echo "so you can see status, stop with Control+C, and restart anytime."
echo "(No administrator password is needed.)"
echo

STATUS_TEXT="$("$PYTHON" "$SUPPORT_DIR/transcriber.py" --manage-autostart status)"
echo "Current status: $STATUS_TEXT"
echo

if [[ "$STATUS_TEXT" == OFF* || "$STATUS_TEXT" == "ON (saved for next login)"* ]]; then
    if [[ "$STATUS_TEXT" == "ON (saved for next login)"* ]]; then
        echo "Auto-Start is saved but is not active yet. This can happen after an older setup."
        PROMPT="Activate Auto-Start now? [Press Enter or type YES, or N to cancel]: "
    else
        PROMPT="Turn ON Auto-Start at Login? [Press Enter or type YES, or N to cancel]: "
    fi
    read -r -p "$PROMPT" CHOICE
    CHOICE_CLEAN="$(printf '%s' "${CHOICE:-}" | /usr/bin/tr '[:upper:]' '[:lower:]' | /usr/bin/tr -d '[:space:]')"
    if [[ -z "$CHOICE_CLEAN" || "$CHOICE_CLEAN" == "y" || "$CHOICE_CLEAN" == "yes" || "$CHOICE_CLEAN" == "on" ]]; then
        echo
        "$PYTHON" "$SUPPORT_DIR/transcriber.py" --manage-autostart on
    else
        echo "Canceled — no change was made."
    fi
else
    EXPECTED_CMD="$APP_DIR/3 Start Transcriber.command"
    if [[ "$STATUS_TEXT" != *"$EXPECTED_CMD"* ]]; then
        echo "Note: Auto-Start currently points to a previous folder location."
        echo "Type U to update it to this folder, Enter to turn it OFF, or K to cancel."
        read -r -p "Choice [Enter = Turn OFF / U = Update folder / K = Cancel]: " CHOICE
        CHOICE_CLEAN="$(printf '%s' "${CHOICE:-}" | /usr/bin/tr '[:upper:]' '[:lower:]' | /usr/bin/tr -d '[:space:]')"
        if [[ "$CHOICE_CLEAN" == "u" || "$CHOICE_CLEAN" == "update" ]]; then
            echo
            "$PYTHON" "$SUPPORT_DIR/transcriber.py" --manage-autostart on
            exit 0
        fi
    else
        read -r -p "Turn OFF Auto-Start at Login? [Press Enter or type YES, or K to keep ON]: " CHOICE
        CHOICE_CLEAN="$(printf '%s' "${CHOICE:-}" | /usr/bin/tr '[:upper:]' '[:lower:]' | /usr/bin/tr -d '[:space:]')"
    fi

    if [[ -z "$CHOICE_CLEAN" || "$CHOICE_CLEAN" == "y" || "$CHOICE_CLEAN" == "yes" || "$CHOICE_CLEAN" == "off" ]]; then
        echo
        "$PYTHON" "$SUPPORT_DIR/transcriber.py" --manage-autostart off
    else
        echo "Canceled — Auto-Start remains ON."
    fi
fi
