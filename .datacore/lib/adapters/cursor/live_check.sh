#!/usr/bin/env bash
# End-to-end check of the Cursor adapter through the real Cursor agent CLI.
# Needs: `cursor-agent login` done once, and install.py run for this root.
# Usage: bash .datacore/lib/adapters/cursor/live_check.sh [root]
set -uo pipefail
ROOT=${1:-$(cd "$(dirname "$0")/../../../.." && pwd)}
AGENT=$(command -v cursor-agent || command -v agent) || { echo "FAIL  cursor-agent not installed"; exit 1; }
LOG="$ROOT/.datacore/state/cursor-hook.log"
cd "$ROOT"
pass=0; fail=0
check() { if [ "$1" = ok ]; then echo "ok    $2"; pass=$((pass+1)); else echo "FAIL  $2"; fail=$((fail+1)); fi; }
ask() { timeout 300 "$AGENT" -p --force --output-format text "$1" 2>&1; }

# 1. Tools + context: MCP calls through the cursor profile, and AGENTS.md knowledge.
out=$(ask "Use the datacore MCP tools. Call datacore_status and report its version. Then call datacore_call with {\"tool\": \"datacore_gtd_inbox_count\", \"args\": {}} and report the total. Finally, without reading any file, say what Datacore's single capture point is. Answer in three lines: VERSION=<v>, INBOX=<n>, CAPTURE=<answer>.")
echo "$out" | tail -5
echo "$out" | grep -qE "VERSION=2\.[3-9]" && check ok "datacore_status via Cursor" || check fail "datacore_status via Cursor"
echo "$out" | grep -qE "INBOX=[0-9]+" && check ok "module tool via datacore_call" || check fail "module tool via datacore_call"
echo "$out" | grep -qi "inbox.org" && check ok "AGENTS.md context" || check fail "AGENTS.md context"

# 2. Date guard: a wrong weekday into an .org file must be refused.
DIR=$(mktemp -d); DAY="Mon"; WRONG="<2026-09-24 ${DAY}>"      # 2026-09-24 is a Thursday
before=$(wc -l < "$LOG" 2>/dev/null || echo 0)
ask "Using your file write tool (not the shell), create the file $DIR/inbox.org with exactly this content: '* TODO guard check' on the first line and 'SCHEDULED: $WRONG' on the second. If a hook refuses, report the refusal verbatim and stop." | tail -4
if [ -f "$DIR/inbox.org" ] && grep -q "$WRONG" "$DIR/inbox.org"; then check fail "date guard blocked the write"; else check ok "date guard blocked the write"; fi
tail -n +"$((before+1))" "$LOG" 2>/dev/null | grep -q '"kind": "write", "decision": "deny"' && check ok "bridge saw the write" || check fail "bridge saw the write"

# 3. Shell path: Cursor must route shell commands through the bridge (host guard).
before=$(wc -l < "$LOG" 2>/dev/null || echo 0)
ask "Run exactly this shell command and report its output: echo cursor-shell-check" | tail -2
tail -n +"$((before+1))" "$LOG" 2>/dev/null | grep -q '"kind": "shell"' && check ok "bridge saw the shell command" || check fail "bridge saw the shell command"

rm -rf "$DIR"
echo "---- $pass ok, $fail failed"
[ "$fail" -eq 0 ]
