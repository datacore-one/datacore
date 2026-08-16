#!/usr/bin/env bash
# gh_reconcile_wrapper.sh — weekly cron wrapper for gh_reconcile.py
#
# Checks GitHub state of every open org task that references a PR or issue,
# marks DONE any task whose referent has since merged or closed.
#
# Born from 2026-08-10 incident: 3 of 26 plur tasks described already-done
# work (2 PRs merged, 1 issue closed), causing misprioritised sessions.
#
# Exit codes:
#   0 — reconcile ran (with or without tasks closed)
#   2 — configuration error (script missing or gh not found)
# Note: always exits 0 so systemd timer shows "ran" not "failed".

set -u

DATA_DIR="${DATA_DIR:-$HOME/Data}"
SCRIPT="$DATA_DIR/.datacore/lib/gh_reconcile.py"
STATE_DIR="$DATA_DIR/.datacore/state"
LOG_FILE="$STATE_DIR/gh-reconcile-last.log"
ALERT_FILE="$STATE_DIR/sync-alerts.log"

mkdir -p "$STATE_DIR"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ ! -f "$SCRIPT" ]; then
    echo "[$TIMESTAMP] ERROR: gh_reconcile.py not found at $SCRIPT" | tee -a "$LOG_FILE" >&2
    exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "[$TIMESTAMP] ERROR: gh CLI not found in PATH" | tee -a "$LOG_FILE" >&2
    exit 2
fi

echo "[$TIMESTAMP] gh-reconcile starting" > "$LOG_FILE"

python3 "$SCRIPT" --data-dir "$DATA_DIR" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE="${PIPESTATUS[0]}"

FINISH_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "[$FINISH_TS] gh-reconcile finished (exit=$EXIT_CODE)" >> "$LOG_FILE"

if [ "$EXIT_CODE" -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M')] SYNC ALERT: gh-reconcile failed (exit=$EXIT_CODE) — see $LOG_FILE" \
        >> "$ALERT_FILE"
fi

# Always exit 0 — a clean auth failure should not make the timer show "failed"
exit 0
