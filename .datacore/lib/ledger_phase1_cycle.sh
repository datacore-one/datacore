#!/bin/bash
# Phase 1 cycle (DIP-0046): ingest -> converge -> project, hourly on every host.
#   ingest    what writers put into org files since the last cycle -> ledger
#   converge  this host's ledger logs with everyone else's (git transport)
#   project   for spaces in Phase 1 only: org/next_actions.org <- ledger
# Order is the whole point: projecting before ingesting loses a hand edit.
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime_shell.sh" || exit 2
finish() {
  local result="$1" why="${2:-}"
  local label=FAIL
  [ "$result" -eq 0 ] && label=OK
  printf "%s phase1-cycle %s rc=%s%s\n" "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$result" "${why:+ ($why)}" > "$STATE/phase1-cycle-status.txt" || return 2
  return "$result"
}
# A runtime that cannot start must still WRITE its status. This used to exit
# before `finish` existed, leaving yesterday's status in place, so the contract
# could only ever report the artifact as "stale" -- never the cause. On
# 2026-09-16 fourteen consecutive runs died on "no usable Python" while the mac
# was awake and on battery, and all the alerts said was the file's age.
# runtime_shell sets and creates $STATE before it probes for a Python, so the
# status can be written even when no interpreter is found.
datacore_runtime_init
rc=$?
if [ "$rc" -ne 0 ]; then
  # (Not `if ! datacore_runtime_init; then rc=$?` -- inside that branch $? is
  # the status of `!` itself, so every failure would be written as OK.)
  STATE="${STATE:-${DATACORE_STATE:-$HOME/.datacore/state}}"
  mkdir -p -- "$STATE" 2>/dev/null
  finish "$rc" "runtime init failed: no usable Python with PyYAML and org-workspace"
  exit "$rc"
fi
cd "$DATACORE_ROOT" || exit 2
echo "=== $(date -u '+%F %H:%MZ') phase-1 cycle ==="
# Converge EVERY space first, Phase 1 or not: the marker that says a space is
# in Phase 1 arrives by pull, and a cycle that pulls only spaces it already
# knows are in Phase 1 never learns about a flip. On 2026-09-06 nine spaces
# were flipped on the mac; the box's cycle regenerated five and left four
# stale until the next fleet sync. Only directories that carry an event log
# are spaces; archives and stray checkouts under the root are not.
# OFFLINE IS A CONDITION, NOT A FAILURE -- which is what ledger_transport says
# in as many words, and this caller was not listening. The mac is a laptop: its
# 02:53Z cycle on 2026-09-18 met a sleeping network ("fetch failed (offline?)",
# ssh timeouts to the Gitea host), wrote FAIL, and alerted about a machine that
# was simply asleep. A fetch that is REFUSED -- auth denied, remote repo missing
# -- is a different sentence from the same classifier and still fails the cycle,
# because nothing about it improves on its own.
offline_only() {
  grep -q '"reason": "fetch failed (offline?)"' "$1" 2>/dev/null
}
rc=0
offline=0
for d in "$DATACORE_ROOT"/[0-9]-*; do
  [ -d "$d/.datacore/events" ] && [ -d "$d/.git" ] || continue
  log="$STATE/phase1-converge-$(basename "$d").log"
  if ! "$PY" "$LIB/ledger_transport.py" converge --space "$d" > "$log" 2>&1; then
    if offline_only "$log"; then
      echo "converge $(basename "$d"): offline; this host will receive it when the network returns"
      offline=$((offline + 1))
    else
      echo "converge $(basename "$d"): failed; see its log"
      rc=1
    fi
  fi
done
# A failed receive may leave a merge in progress. Never ingest or replace
# files from that intermediate state.
if [ "$rc" -ne 0 ]; then finish "$rc"; exit $?; fi
PHASE1=()
for d in "$DATACORE_ROOT"/[0-9]-*; do
  if [ -d "$d/.datacore/events" ] && [ "$(cat "$d/.datacore/ledger-phase" 2>/dev/null | tr -d '[:space:]')" = "1" ]; then
    PHASE1+=("$d")
  fi
done
if [ "${#PHASE1[@]}" -eq 0 ]; then echo "no space in Phase 1; nothing to do"; finish 0; exit $?; fi
"$PY" "$LIB/ledger_ingest_org.py" --root "$DATACORE_ROOT" > "$STATE/phase1-ingest.log" 2>&1
rc=$?
echo "ingest rc=$rc"
# Existing IDs do not prove that edited bodies/properties reached the ledger.
# On any ingest failure preserve every source file and stop before projection.
if [ "$rc" -ne 0 ]; then finish "$rc"; exit $?; fi
for s in "${PHASE1[@]}"; do
  log="$STATE/phase1-converge-$(basename "$s").log"
  if ! "$PY" "$LIB/ledger_transport.py" converge --space "$s" > "$log" 2>&1; then
    if offline_only "$log"; then
      echo "converge $(basename "$s"): offline; this host will receive it when the network returns"
      offline=$((offline + 1))
    else
      echo "converge $(basename "$s"): failed; see its log"
      rc=1
    fi
  fi
done
if [ "$rc" -ne 0 ]; then finish "$rc"; exit $?; fi
# `... | grep -v authored ; echo "rc=$?"` read GREP's status, not the
# projector's, so this printed `project rc=0` unconditionally -- a projection
# crash, and every REFUSED line, exited 0 and alerted nobody. PIPESTATUS[0] is
# the projector's own status, and it now feeds the script's exit code.
"$PY" "$LIB/ledger_project_org.py" --all 2>&1 | grep -v "authored"
prc=${PIPESTATUS[0]}
echo "project rc=$prc"
[ "$offline" -gt 0 ] && echo "offline space(s) this cycle: $offline"
[ "$prc" -eq 0 ] || rc=$prc

finish "$rc"
exit $?
