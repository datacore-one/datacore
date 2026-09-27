#!/bin/bash
# Sync Claude Code conversation logs to Datacore traces
# Runs daily via cron — rsync only copies new/changed files
#
# Recursive: Claude Code writes a subagent's transcript under
# <project>/<session>/subagents/agent-*.jsonl, not beside the session's own
# file. A top-level-only copy (`$dir*.jsonl`) left every subagent transcript
# out of the mirror (MEM-67: 621 missing on the mac). Only *.jsonl travels;
# the directories that hold them are recreated, empty ones are not.

# TRACES_DEST: where the copy goes. The workstation's lives in the personal
# space; a server keeps its copy beside its own state (agent_host_setup.sh).
DEST="${TRACES_DEST:-$HOME/Data/0-personal/traces/claude-code}"
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
# One line per run, so a scheduled copy leaves a fresh artifact to verify.
echo "$(date -u +%FT%TZ) sync_traces: $(find "$DEST" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ') transcript(s) in $DEST"
