# Isolated zshenv for Audio Transcriber's post-Ctrl+C interactive prompt.
# Prevents system/user startup scripts from altering history or directory.
unsetopt GLOBAL_RCS
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
