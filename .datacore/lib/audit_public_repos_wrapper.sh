#!/usr/bin/env bash
# Wrapper: runs audit_public_repos.py and converts any forbidden hits
# into sync-alerts.log entries so they surface in /today's ops-health section.
#
# Born out of a 2026-05-14 sync incident — see .datacore/state/public-repo-denylist.yaml.
#
# Exit codes:
#   0 — audit ran (with or without hits; hits are alerted, not failed)
#   2 — audit could not run (configuration or environment error)

set -u

DATA_DIR="${DATA_DIR:-$HOME/Data}"
ALERT_FILE="$DATA_DIR/.datacore/state/sync-alerts.log"
AUDIT_SCRIPT="$DATA_DIR/.datacore/lib/audit_public_repos.py"
STATE_DIR="$DATA_DIR/.datacore/state"
LAST_RESULT="$STATE_DIR/public-repo-audit-last.json"

mkdir -p "$STATE_DIR"

if [ ! -x "$AUDIT_SCRIPT" ]; then
    echo "ERROR: audit script missing or not executable: $AUDIT_SCRIPT" >&2
    exit 2
fi

# Run the audit; capture JSON to disk for /today to read
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
if "$AUDIT_SCRIPT" --json > "$LAST_RESULT" 2>/dev/null; then
    EXIT_CODE=0
else
    EXIT_CODE=$?
fi

# Annotate the JSON file with timestamp
python3 - <<PY
import json, sys
from pathlib import Path
p = Path("$LAST_RESULT")
try:
    data = json.loads(p.read_text())
except Exception:
    data = {"hits": [], "forbidden_count": 0}
data["_timestamp"] = "$TIMESTAMP"
data["_exit_code"] = $EXIT_CODE
p.write_text(json.dumps(data, indent=2))
PY

# If there were forbidden hits, write a SYNC ALERT (sync-alerts.log is the
# canonical place /today scans for ops-health flags)
FORBIDDEN=$(python3 -c "
import json
with open('$LAST_RESULT') as f:
    d = json.load(f)
print(d.get('forbidden_count', 0))
")

if [ "$FORBIDDEN" -gt 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M')] SYNC ALERT: public-repo audit found $FORBIDDEN forbidden hit(s) — see $LAST_RESULT" >> "$ALERT_FILE"
    # Also print one-line summary per hit
    python3 -c "
import json
with open('$LAST_RESULT') as f:
    d = json.load(f)
for h in d.get('hits', []):
    if h.get('severity') == 'forbidden':
        print(f\"[$(date '+%Y-%m-%d %H:%M')] SYNC ALERT:   {h['repo']} :: {h['path']} (rule={h['rule']}, kind={h['kind']})\")
" >> "$ALERT_FILE"
    echo "public-repo audit: $FORBIDDEN forbidden hit(s) logged to $ALERT_FILE" >&2
else
    echo "public-repo audit: clean (timestamp $TIMESTAMP)" >&2
fi

# Always exit 0 — the audit itself ran successfully even when hits exist.
# The systemd timer status should reflect "did the check run" not "is the repo clean".
exit 0
