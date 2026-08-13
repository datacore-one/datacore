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

# ── keep the v2-runner checkout current ──────────────────────────────────────
# Six cron entries run this machine's detectors and job_verify from
# ~/.datacore/v2-runner — a checkout SEPARATE from ~/Data, deliberately,
# because ~/Data is a live shared working tree that agents edit underneath a
# running job. Nothing was refreshing it, so this Mac ran 2026-08-12 code while
# every fix reached the servers, and a threshold already corrected in the repo
# (mac-lens-sync max_age 2h -> 14h) kept firing false alerts from the stale copy
# for hours.
#
# It lives HERE rather than in its own cron entry because `crontab` cannot be
# edited non-interactively on macOS (TCC blocks it), and because this script
# already runs every 5 minutes FROM ~/Data — so it is itself always current.
# A fix to the refresh logic ships with an ordinary pull.
#
# Hourly, not every run: a network fetch every 5 minutes forever is waste, and
# the drift this repairs takes hours to matter.
RUNNER="$HOME/.datacore/v2-runner"
STAMP="$HOME/.datacore/state/.runner-refresh-stamp"
# -e, not -d: this checkout is a git WORKTREE, whose .git is a FILE
# pointing at the real gitdir. A -d test silently skipped the refresh.
if [[ -e "$RUNNER/.git" ]]; then
  now=$(date +%s)
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  if (( now - last >= 3600 )); then
    mkdir -p "$(dirname "$STAMP")"
    # Merge, never rebase (DIP-0046). --ff-only would be safer still, but this
    # checkout is read-only in practice, so a merge cannot strand local work.
    if git -C "$RUNNER" pull -q --no-rebase origin main 2>&1 | tail -2 >> "$LOG_FILE"; then
      echo "$now" > "$STAMP"
      # ASSERT the outcome, do not assume it. The whole point is that the
      # detectors run current code; a pull that "succeeded" while leaving the
      # checkout behind origin would restore exactly the silent drift this
      # exists to remove. The runner sits on a detached HEAD by design (it is
      # a worktree of ~/Data, which already holds main), so compare to
      # origin/main directly.
      rhead=$(git -C "$RUNNER" rev-parse HEAD 2>/dev/null)
      ohead=$(git -C "$RUNNER" rev-parse origin/main 2>/dev/null)
      if [[ "$rhead" == "$ohead" ]]; then
        log "v2-runner current -> ${rhead:0:7}"
      else
        log "v2-runner DRIFT: at ${rhead:0:7}, origin/main is ${ohead:0:7} — detectors are running stale code"
      fi
    else
      log "v2-runner refresh FAILED — detectors may be running stale code"
    fi
  fi
fi
