#!/usr/bin/env bash
# The 2026-09-19 audit trio, as launchd runs them.
#
# WHY A WRAPPER AND NOT THREE PLISTS OF INLINE PYTHON. Each of these writes an
# artifact that a job contract asserts on, and the contract fails if the
# artifact is stale. That makes "did it run" as important as "did it pass", and
# a wrapper is the one place to guarantee both: the artifact is written on
# every path, including the failure paths, so a contract can tell "ran and
# found something" from "never ran at all".
#
# WHY LAUNCHD AND NOT CRON. This is a laptop. cron drops runs missed while the
# lid was shut; launchd catches up. The phase-1 cycle moved for the same reason
# on 2026-09-09 and its plist says so.
set -uo pipefail
LIB="${DATACORE_LIB:-$HOME/Data/.datacore/lib}"
STATE="${DATACORE_STATE:-$HOME/.datacore/state}"
PY="${DATACORE_PYTHON:-python3}"
mkdir -p "$STATE"

case "${1:-}" in
  invariants)
    "$PY" "$LIB/ledger_invariants.py" > "$STATE/ledger-invariants.log" 2>&1
    ;;
  config)
    "$PY" "$LIB/config_resolution_probe.py" > "$STATE/config-resolution.log" 2>&1
    ;;
  canary-run)
    "$PY" "$LIB/delegation_canary.py" --run --space "$HOME/Data/8-firm" \
      --assignee miles > "$STATE/delegation-canary.log" 2>&1
    ;;
  canary-check)
    "$PY" "$LIB/delegation_canary.py" --check --space "$HOME/Data/8-firm" \
      > "$STATE/delegation-canary-check.log" 2>&1
    ;;
  *)
    echo "usage: audit_trio_run.sh {invariants|config|canary-run|canary-check}" >&2
    exit 2
    ;;
esac
rc=$?
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) audit-trio ${1} rc=$rc" >> "$STATE/audit-trio.log"
exit $rc
