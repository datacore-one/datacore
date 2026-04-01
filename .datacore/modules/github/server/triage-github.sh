#!/usr/bin/env bash
# triage-github.sh — 6am fallback: check nightshift, triage GitHub if it missed
#
# Runs at 06:00 UTC on the nightshift server.
# If nightshift ran overnight, exits cleanly (nothing to do).
# If nightshift did NOT run, executes GitHub triage and creates :AI:github: tasks.

set -euo pipefail

DATA_DIR="${DATA_DIR:-$HOME/Data}"
MODULE_DIR="$DATA_DIR/.datacore/modules/github"
CACHE_DIR="$MODULE_DIR/data"
LOG_PREFIX="[github-triage]"

echo "$LOG_PREFIX Starting at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# --- Check if nightshift ran overnight ---
YESTERDAY=$(date -u -d 'yesterday' '+%Y-%m-%d' 2>/dev/null || date -u -v-1d '+%Y-%m-%d')
TODAY=$(date -u '+%Y-%m-%d')
INBOX="$DATA_DIR/0-personal/0-inbox"

nightshift_ran=false
for d in "$YESTERDAY" "$TODAY"; do
    if ls "$INBOX"/nightshift-*"$d"* 1>/dev/null 2>&1; then
        nightshift_ran=true
        break
    fi
done

if [ "$nightshift_ran" = true ]; then
    echo "$LOG_PREFIX Nightshift ran overnight. No fallback triage needed."
    exit 0
fi

echo "$LOG_PREFIX Nightshift did NOT run overnight. Running GitHub triage..."

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
python3 "$MODULE_DIR/lib/github_scanner.py" \
    --username plur9 \
    --orgs "$ORGS" \
    --hours 24 \
    --cache "$CACHE_DIR/scan_cache.json" \
    --format json > /dev/null

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

echo "$LOG_PREFIX Completed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
