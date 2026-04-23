#!/bin/bash
# Sync Claude Code conversation logs to Datacore traces
# Runs daily via cron — rsync only copies new/changed files

DEST="$HOME/Data/0-personal/traces/claude-code"
SRC="$HOME/.claude/projects"

for dir in "$SRC"/*/; do
    dirname=$(basename "$dir")
    # Only sync dirs that have .jsonl files
    if compgen -G "$dir"*.jsonl > /dev/null 2>&1; then
        mkdir -p "$DEST/$dirname"
        rsync -a "$dir"*.jsonl "$DEST/$dirname/"
    fi
done
