#!/usr/bin/env bash
# sync_state_from_nightshift.sh — Pull venture/crew state from the nightshift
# server (the authoritative source) onto the local Mac.
#
# Why: nightshift owns the heartbeat. Local writes are NOT authoritative.
# The Mac desktop app reads filesystem files, so we mirror the relevant
# state subset from nightshift periodically.
#
# What gets synced (READ-ONLY mirror — never push back):
#   - [space]/.datacore/state/heartbeat.json     (per-venture liveness)
#   - [space]/.datacore/state/decisions-pending.json (when daemon emits it)
#   - .datacore/state/agents/{data,tris,miles}.json  (crew runtime state)
#
# What is NOT synced:
#   - cadence-log.yaml — already in git via the space repo
#   - hypothesis state — already in git
#   - heartbeat.log    — too noisy; observable via journalctl on nightshift
#
# Schedule: launchd timer (~/Library/LaunchAgents/io.datacore.state-sync.plist)
# fires every 5 minutes. Manual run: just exec this script.
#
# Idempotent. Errors are logged to ~/.datacore/state-sync.log (best-effort).

set -uo pipefail

DATA_DIR="${DATA_DIR:-$HOME/Data}"
REMOTE="${REMOTE:-nightshift}"
LOG_FILE="${LOG_FILE:-$HOME/.datacore/state-sync.log}"

mkdir -p "$(dirname "$LOG_FILE")"

ts() { date -u "+%Y-%m-%dT%H:%M:%SZ"; }
log() { printf '[%s] %s\n' "$(ts)" "$*" >> "$LOG_FILE"; }

log "begin sync from $REMOTE"

# 1) Crew agent state (single directory, fixed location)
mkdir -p "$DATA_DIR/.datacore/state/agents"
if rsync -a --include='*.json' --exclude='*' \
    "$REMOTE:Data/.datacore/state/agents/" \
    "$DATA_DIR/.datacore/state/agents/" 2>>"$LOG_FILE"; then
  log "crew state ok"
else
  log "crew state FAILED (rc=$?)"
fi

# 2) Per-venture heartbeat.json + decisions-pending.json
#    Iterate the local space dirs; mirror state per venture.
shopt -s nullglob
synced=0
failed=0
for space_dir in "$DATA_DIR"/[1-9]-*/; do
  space_name=$(basename "$space_dir")
  # 6-meridian is intentionally not written by daemon — skip
  if [[ "$space_name" == "6-meridian" ]]; then
    continue
  fi

  mkdir -p "$space_dir.datacore/state"
  if rsync -a --include='heartbeat.json' --include='decisions-pending.json' \
        --exclude='*' \
        "$REMOTE:Data/$space_name/.datacore/state/" \
        "$space_dir.datacore/state/" 2>>"$LOG_FILE"; then
    synced=$((synced + 1))
  else
    failed=$((failed + 1))
    log "  $space_name FAILED (rc=$?)"
  fi
done

log "venture state: $synced ok, $failed failed"
log "end sync"

# Quick visibility for ad-hoc runs
if [[ -t 1 ]]; then
  echo "Synced from $REMOTE: crew state + $synced venture heartbeats ($failed failed)"
  echo "Log: $LOG_FILE"
fi
