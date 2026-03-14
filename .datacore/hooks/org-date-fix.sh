#!/bin/bash
# PostToolUse hook: auto-fix wrong day-of-week in org/md files after Edit/Write
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Process .org and .md files (journals, notes with org-mode dates)
if [[ "$FILE_PATH" == *.org ]] || [[ "$FILE_PATH" == *.md ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  python3 "$SCRIPT_DIR/lib/org_date_hook.py" "$FILE_PATH"
fi
exit 0
