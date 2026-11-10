# Minimal interactive zsh config for Audio Transcriber's Ctrl+C -> Up-Arrow + Enter workflow.
export AT_RESTART_SHELL=1
HISTSIZE=50
SAVEHIST=0
unset HISTFILE
setopt HIST_IGNORE_DUPS

# Standard Emacs keybindings guarantee Up-Arrow / Down-Arrow recall history on every Mac.
bindkey -e

PROMPT="audio-transcriber % "

restart() {
    "$AT_START_CMD"
}

if [[ -n "${AT_START_CMD:-}" ]]; then
    # Pre-load the properly shell-escaped start command at the top of Up-Arrow history (-r keeps backslashes intact!).
    print -r -s -- "${(q)AT_START_CMD}"
fi

echo
echo "================================================================"
echo "Audio Transcriber is stopped."
echo "  - To restart : press Up-Arrow, then Enter  (or type: restart)"
echo "  - To quit    : close this Terminal window"
echo "================================================================"
echo
