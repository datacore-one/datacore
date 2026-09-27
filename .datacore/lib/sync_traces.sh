#!/bin/bash
# Sync Claude Code conversation logs to Datacore traces
# Runs daily via cron — rsync only copies new/changed files
#
# Recursive: Claude Code writes a subagent's transcript under
# <project>/<session>/subagents/agent-*.jsonl, not beside the session's own
# file. A top-level-only copy (`$dir*.jsonl`) left every subagent transcript
# out of the mirror (MEM-67: 621 missing on the mac). Only *.jsonl travels;
# the directories that hold them are recreated, empty ones are not.

DEST="$HOME/Data/0-personal/traces/claude-code"
SRC="$HOME/.claude/projects"

for dir in "$SRC"/*/; do
    dirname=$(basename "$dir")
    # Only sync dirs that hold a transcript at any depth
    if [ -n "$(find "$dir" -name '*.jsonl' -print -quit 2>/dev/null)" ]; then
        mkdir -p "$DEST/$dirname"
        rsync -a --prune-empty-dirs --include='*/' --include='*.jsonl' --exclude='*' \
            "$dir" "$DEST/$dirname/"
    fi
done
