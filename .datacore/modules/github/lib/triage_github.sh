#!/usr/bin/env bash
# triage-github.sh — daily GitHub triage: scan mentions, comments, org activity
#
# Invoked by a host's scheduler (systemd timer or cron). Always runs — no
# fallback heuristic. The previous "skip if nightshift ran overnight"
# check was wrong: the AI-task runner (nightshift-overnight) does not
# perform GitHub triage; it processes :AI: tagged tasks. The check
# always shortcircuited because nightshift-exec-* files are produced
# on every overnight run, regardless of whether GitHub was triaged.
#
# Mirrors the triage-email.sh design: always runs, feeds inbox.org
# entries so subsequent inbox processing can classify and route them.

set -euo pipefail

# Host-agnostic by design: this file lives in the PUBLIC core repo, so it
# carries no usernames, paths or credentials. Everything host-specific comes
# from the environment — DATA_DIR, GITHUB_TRIAGE_USERNAME, GH_TOKEN — which
# each host supplies from its own private config.
DATA_DIR="${DATA_DIR:-$HOME/Data}"
MODULE_DIR="$DATA_DIR/.datacore/modules/github"
CACHE_DIR="$MODULE_DIR/data"
LOG_PREFIX="[github-triage]"

echo "$LOG_PREFIX Starting at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# --- Step 1: Repo discovery ---
echo "$LOG_PREFIX Discovering repos..."
python3 "$MODULE_DIR/lib/repo_discovery.py" \
    --data-dir "$DATA_DIR" > /dev/null 2>&1 || true

# --- Step 2: Scan GitHub ---
ORGS=$(python3 -c "
import json
from pathlib import Path
repos_file = Path('$CACHE_DIR/repos.json')
if repos_file.exists():
    d = json.loads(repos_file.read_text())
    print(','.join(d.get('orgs', [])))
else:
    print('')
")

if [ -z "$ORGS" ]; then
    echo "$LOG_PREFIX No orgs found. Run repo discovery first."
    exit 1
fi

echo "$LOG_PREFIX Scanning orgs: $ORGS"
# The scanner exits 2 when any gh query failed. Do not swallow it: a scan whose
# every query failed produces the same empty cache as a genuinely quiet day, and
# that ambiguity hid an unauthenticated `gh` on the CoS box for 23 days
# (2026-07-19 .. 2026-08-10) behind "created: 0, errors: 0" and "Completed".
SCAN_RC=0
python3 "$MODULE_DIR/lib/github_scanner.py" \
    --username "${GITHUB_TRIAGE_USERNAME:-plur9}" \
    --orgs "$ORGS" \
    --hours 24 \
    --cache "$CACHE_DIR/scan_cache.json" \
    --format json > /dev/null || SCAN_RC=$?

if [ "$SCAN_RC" != "0" ]; then
    echo "$LOG_PREFIX SCAN INCOMPLETE (scanner exit $SCAN_RC) — one or more gh queries failed."
    echo "$LOG_PREFIX Any 'no tasks created' below means 'could not look', NOT 'nothing to do'."
    echo "$LOG_PREFIX Check: gh auth status   (needs GH_TOKEN in the environment, scopes repo+read:org)"
fi

# --- Step 3: Create tasks ---
REPOS_FILE="$CACHE_DIR/repos.json"
SCAN_FILE="$CACHE_DIR/scan_cache.json"

if [ -f "$SCAN_FILE" ] && [ -f "$REPOS_FILE" ]; then
    echo "$LOG_PREFIX Creating tasks..."
    python3 "$MODULE_DIR/lib/task_creator.py" \
        --scan-file "$SCAN_FILE" \
        --repos-file "$REPOS_FILE" \
        --data-dir "$DATA_DIR"
else
    echo "$LOG_PREFIX Missing scan or repos cache. Skipping task creation."
fi

if [ "$SCAN_RC" != "0" ]; then
    echo "$LOG_PREFIX FAILED at $(date -u '+%Y-%m-%d %H:%M:%S UTC') — scan incomplete"
    exit "$SCAN_RC"
fi

echo "$LOG_PREFIX Completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
