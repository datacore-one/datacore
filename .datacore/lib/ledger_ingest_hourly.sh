#!/bin/bash
# Hourly ledger ingest: sync org -> ledger.
#
# CHEAP PASS ONLY. This script runs ingest and nothing else. Shadow_check and
# checkpoint are verification passes that live in ledger_daily.sh; they run
# once at 05:35 after the 05:00 ingest of this script has already completed.
#
# WHY SPLIT. Agent claim timers fire every 15 minutes on 4 hosts, so delegated
# work lands in the ledger within minutes. Human org edits (task captures,
# state changes) used to wait up to 24 h. Hourly ingest drops that to ~1 h,
# which also keeps shadow_check's drift gate honest: stale ledger data inflated
# the apparent drift score and could block the DIP-0046 F2 Phase 1 flip.
#
# ORDER CONSTRAINT. The daily verification job (ledger_daily.sh) at 05:35 must
# always run AFTER an ingest. The hourly schedule fires at :00 of every hour,
# so the 05:00 run completes well before 05:35. Do not move 05:35 earlier than
# 05:01 without also adjusting the hourly schedule.
#
# SERVER. Runs on winston (always-on Linux box) via cron:
#   0 * * * * DATACORE_ROOT=/home/deploy/Data /home/deploy/Data/.datacore/lib/ledger_ingest_hourly.sh >> /home/deploy/.datacore/state/ledger-ingest-cron.log 2>&1
set -u
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime_shell.sh" || exit 2
datacore_runtime_init || exit $?
echo "python: $PY"

# THE CYCLE'S LOCK, AND THE CYCLE'S VERDICT (audit B-F1). This ingest runs
# the orphan sweep, which dismisses a ledger item absent from the generated
# file twice, 30+ minutes apart -- terminally, as `housekeeping`. When a space's
# converge fails, its projection is not refreshed, so fresh items from other
# hosts are "absent" from a stale file. While the box cycle was aborted on
# 2026-09-26 05:25-08:25Z, this ingest kept sweeping and dismissed two new
# 5-plur tasks. So:
#   - it takes the phase-1 cycle lock (same mkdir + pid protocol as
#     ledger_phase1_cycle.sh), so it never overlaps a cycle;
#   - it skips every Phase-1 space whose last converge failed (the cycle leaves
#     $STATE/phase1-converge-<space>.failed and removes it on success).
LOCK="$STATE/phase1-cycle.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
    echo "a phase-1 cycle is running (pid $owner); it ingests this hour"
    exit 0
  fi
  echo "breaking a stale cycle lock (pid ${owner:-unknown} is gone)"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || { echo "could not take the cycle lock"; exit 0; }
fi
echo $$ > "$LOCK/pid"
VIEW=""
trap 'rm -rf "$LOCK" ${VIEW:+"$VIEW"}' EXIT

echo "=== $(date '+%F %T') ledger ingest (hourly) ==="
ROOT_ARGS=()   # none: the sweep reads DATACORE_ROOT, as it always has
skipped=""
for d in "$DATACORE_ROOT"/[0-9]-*; do
  name="$(basename "$d")"
  [ -f "$STATE/phase1-converge-$name.failed" ] || continue
  [ "$(cat "$d/.datacore/ledger-phase" 2>/dev/null | tr -d '[:space:]')" = "1" ] || continue
  echo "ingest $name: SKIPPED, its last converge failed: $(head -1 "$STATE/phase1-converge-$name.failed")"
  skipped="$skipped $name "
done
if [ -n "$skipped" ]; then
  # The sweep takes a root, not a list: give it one without the skipped spaces.
  VIEW="$(mktemp -d "$STATE/ledger-ingest-root.XXXXXX")" || exit 2
  for d in "$DATACORE_ROOT"/[0-9]-*; do
    [ -d "$d" ] || continue
    case "$skipped" in *" $(basename "$d") "*) continue;; esac
    ln -s "$d" "$VIEW/$(basename "$d")"
  done
  ROOT_ARGS=(--root "$VIEW")
fi
# WRITE BESIDE IT, THEN RENAME. `> log` truncates at the START of a run that
# then takes minutes to scan ten spaces, so the log is EMPTY for that whole
# window -- every hour, on the hour. box-ledger-ingest reads exactly that file,
# winston's verifier runs on the hour too, and on 2026-09-18 at 10:00 it read
# the empty file and paged: "regex '0 space(s) failed' did not match -- file is
# empty". The ingest had done nothing wrong.
#
# A rename within the same directory is atomic, so a reader sees either the
# previous complete log or the new one, never a half-written one.
out="$STATE/ledger-ingest.log"
tmp="$(mktemp "$out.XXXXXX")" || exit 2
"$PY" "$LIB/ledger_ingest_org.py" ${ROOT_ARGS[@]+"${ROOT_ARGS[@]}"} > "$tmp" 2>&1
ingest_rc=$?
mv -f "$tmp" "$out" || exit 2
echo "ingest rc=$ingest_rc"

exit $ingest_rc
