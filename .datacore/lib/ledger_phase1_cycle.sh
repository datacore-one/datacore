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

# ONE CYCLE AT A TIME. Two callers now run this on a laptop: the hourly schedule
# and the visitor join that fires on wake (visitor_join.py, 2026-09-21), and a
# person can always run it by hand while cron does. Converge takes its own
# transport lock, but ingest and project do not, and two ingests reading the
# same org edit is the kind of overlap that stays invisible until the day it
# writes a fact twice.
#
# mkdir is atomic on every POSIX filesystem and flock(1) does not exist on
# macOS. The lock names its owner so a cycle that was KILLED does not block the
# next one forever: a lock whose process is gone is broken, not honoured.
# Losing the race is not a failure -- the other cycle is doing this work -- so
# the status file is left exactly as that cycle will write it.
LOCK="$STATE/phase1-cycle.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
    echo "another phase-1 cycle is running (pid $owner); leaving it to finish"
    exit 0
  fi
  echo "breaking a stale cycle lock (pid ${owner:-unknown} is gone)"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || { echo "could not take the cycle lock"; exit 0; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
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
  # The reason may be prefixed -- a push reports `push fetch failed (offline?)`,
  # since the same classifier names both halves of the transport.
  grep -q '"reason": "[^"]*fetch failed (offline?)"' "$1" 2>/dev/null
}
# ONE SPACE'S CONVERGE IS ONE SPACE'S CONVERGE (SYN-7, audit B-F4). This used
# to end the whole cycle on any failed converge: one refused autosave in
# 2-datacore on 2026-09-26 left nightshift ingesting and projecting NOTHING, for
# every space, from 04:25Z on -- 69 whole-host aborts on nightshift and 10 on
# the box in three weeks. A failed receive may leave THAT space mid-merge, so
# that space is neither ingested nor projected; the others carry on, and the
# cycle still ends FAIL so the stuck space stays visible.
#
# The verdict is also left on disk, one marker per failed space, so the box's
# standalone hourly ingest (ledger_ingest_hourly.sh) skips the same spaces
# rather than sweeping them against a projection this cycle did not refresh
# (audit B-F1).
converge_reason() {
  # The transport prints {"ok":..., "reason": "..."}; fall back to its last line.
  local r
  r="$(sed -n 's/^[[:space:]]*"reason": "\(.*\)",\{0,1\}[[:space:]]*$/\1/p' "$1" 2>/dev/null | head -1)"
  [ -n "$r" ] || r="$(grep -v '^[[:space:]]*$' "$1" 2>/dev/null | tail -1)"
  printf '%s' "${r:-no output}"
}
FAILED_SPACES=" "
converge_one() {
  # converge_one <space dir>: 0 converged or offline, 1 failed (and recorded).
  local d="$1" name log
  name="$(basename "$d")"
  log="$STATE/phase1-converge-$name.log"
  if "$PY" "$LIB/ledger_transport.py" converge --space "$d" > "$log" 2>&1; then
    rm -f "$STATE/phase1-converge-$name.failed"
    return 0
  fi
  if offline_only "$log"; then
    echo "converge $name: offline; this host will receive it when the network returns"
    offline=$((offline + 1))
    return 0
  fi
  echo "converge $name: FAILED: $(converge_reason "$log") (skipping this space; see $log)"
  converge_reason "$log" > "$STATE/phase1-converge-$name.failed"
  FAILED_SPACES="$FAILED_SPACES$name "
  rc=1
  return 1
}
rc=0
offline=0
for d in "$DATACORE_ROOT"/[0-9]-*; do
  [ -d "$d/.datacore/events" ] && [ -d "$d/.git" ] || continue
  converge_one "$d"
done
failed_now() { case "$FAILED_SPACES" in *" $1 "*) return 0;; esac; return 1; }
PHASE1=()
for d in "$DATACORE_ROOT"/[0-9]-*; do
  if [ -d "$d/.datacore/events" ] && [ "$(cat "$d/.datacore/ledger-phase" 2>/dev/null | tr -d '[:space:]')" = "1" ]; then
    failed_now "$(basename "$d")" || PHASE1+=("$d")
  fi
done
if [ "${#PHASE1[@]}" -eq 0 ]; then
  if [ "$rc" -ne 0 ]; then echo "no healthy Phase-1 space left to ingest or project"; finish "$rc"; exit $?; fi
  echo "no space in Phase 1; nothing to do"; finish 0; exit $?
fi
# The sweep takes a root, not a list. When a space failed to converge, hand it
# a root without that space: a directory of links to every other space, so the
# failed one is neither ingested nor orphan-swept from its possibly half-merged
# tree. (org_space treats a whole-space alias as the space itself.)
INGEST_ROOT="$DATACORE_ROOT"
if [ "$FAILED_SPACES" != " " ]; then
  VIEW="$(mktemp -d "$STATE/phase1-ingest-root.XXXXXX")" || { finish 2; exit 2; }
  trap 'rm -rf "$LOCK" "$VIEW"' EXIT
  for d in "$DATACORE_ROOT"/[0-9]-*; do
    [ -d "$d" ] || continue
    failed_now "$(basename "$d")" || ln -s "$d" "$VIEW/$(basename "$d")"
  done
  INGEST_ROOT="$VIEW"
  echo "ingest: skipping${FAILED_SPACES}- its converge failed"
fi
"$PY" "$LIB/ledger_ingest_org.py" --root "$INGEST_ROOT" > "$STATE/phase1-ingest.log" 2>&1
irc=$?
echo "ingest rc=$irc"
# The reasons belong in THIS log (audit B-F13): phase1-ingest.log is rewritten
# every run, and three weeks of cycle logs could say that 2-datacore failed on
# 09-23 but never why.
grep ' FAILED:' "$STATE/phase1-ingest.log" 2>/dev/null | sed 's/^/ingest /'
# ONE SPACE'S FAULT IS ONE SPACE'S FAULT. Existing IDs do not prove that edited
# bodies and properties reached the ledger, so a space whose ingest failed must
# NOT be projected over. But the sweep already isolates per space -- it catches
# each space's exception and names it -- and stopping the whole run here threw
# away the healthy spaces' projections with it: on 2026-09-18 one duplicate
# :ID: in 5-plur left nightshift projecting NOTHING, for nine spaces, from
# 10:25Z until it was repaired by hand.
#
# So skip the spaces the sweep named and project the rest. The cycle still ends
# FAIL, because a space really is stuck and that has to stay visible.
SKIP=" $(sed -n 's/^\([0-9][^ ]*\)  *FAILED:.*/\1/p' "$STATE/phase1-ingest.log" | tr '\n' ' ')"
# `rc` stays the CONVERGE verdict; the ingest verdict is carried separately and
# folded into the cycle's status at the end, so a stuck space fails the cycle
# without silencing the projection of every healthy one.
if [ "$irc" -ne 0 ] && [ "$SKIP" = " " ]; then
  # Failed without naming a space: the sweep itself did not run, so nothing is
  # known to be safe to project.
  echo "ingest FAILED without naming a space: $(grep -v '^[[:space:]]*$' "$STATE/phase1-ingest.log" 2>/dev/null | tail -1)"
  finish "$irc"; exit $?
fi
[ "$irc" -ne 0 ] && echo "ingest failed for:$SKIP- projecting the rest"
# Publish what was ingested. Same per-space rule: a space whose publish fails
# is not projected (its tree may be mid-merge), the others are.
for s in "${PHASE1[@]}"; do
  converge_one "$s"
done
# `... | grep -v authored ; echo "rc=$?"` read GREP's status, not the
# projector's, so this printed `project rc=0` unconditionally -- a projection
# crash, and every REFUSED line, exited 0 and alerted nobody. PIPESTATUS[0] is
# the projector's own status, and it now feeds the script's exit code.
prc=0
for s in "${PHASE1[@]}"; do
  name=$(basename "$s")
  case "$SKIP" in *" $name "*) echo "project $name: skipped, its ingest failed"; continue;; esac
  if failed_now "$name"; then echo "project $name: skipped, its converge failed"; continue; fi
  "$PY" "$LIB/ledger_project_org.py" --space "$name" 2>&1 | grep -v "authored"
  [ "${PIPESTATUS[0]}" -eq 0 ] || prc=${PIPESTATUS[0]}
done
echo "project rc=$prc"
[ "$offline" -gt 0 ] && echo "offline space(s) this cycle: $offline"
[ "$FAILED_SPACES" != " " ] && echo "converge failed for:$FAILED_SPACES- the other spaces ran"
[ "$prc" -eq 0 ] || rc=$prc
[ "$irc" -eq 0 ] || rc=$irc

why=""
[ "$FAILED_SPACES" != " " ] && why="converge failed:${FAILED_SPACES% }"
finish "$rc" "$why"
exit $?
