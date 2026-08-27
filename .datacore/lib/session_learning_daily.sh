#!/bin/bash
# Nightly learning sweep: catch stragglers, then learn from the day, once.
#
# ORDER IS LOAD-BEARING, same shape as ledger_daily.sh.
#
#   1. `--backfill 2 --status pending` archives any session the SessionEnd hook
#      missed. The hook does not fire when a terminal is closed outright, when
#      the 60s timeout is hit on a very large transcript, or when the machine
#      is force-restarted. Running the sweep first would learn from a day with
#      holes in it and then mark that day done.
#   2. The sweep itself, over EVERY day that still has pending sessions.
#
# `--backlog`, NOT yesterday-only. A yesterday-only sweep cannot revisit an
# older day, so anything one night left behind was stranded for good. On
# 2026-08-18 that was 21 sessions: a PATH bug had killed the sweep outright for
# two days, and even after the fix the schedule would only ever have picked up
# the most recent day. The queue is per-session; the schedule now matches it.
#
# LAUNCHD, NOT CRON. macOS cron does not run missed jobs, and a laptop asleep
# at 05:00 is the normal case, not the exception — the ledger's own daily job
# was moved for exactly this reason on 2026-08-12 after the Mac slept through
# 07:40 and the job silently did not run. launchd runs a missed
# StartCalendarInterval job on wake.
#
# THIS MUST RUN ON THE MAC. The session archive lives under
# .datacore/state/, which is gitignored and machine-local — sessions happen
# here, so the corpus is here. Unlike the ledger, this cannot move to winston.
set -u

# HOLD A POWER ASSERTION FOR THE WHOLE RUN. launchd starts a missed job on wake,
# but nothing stops the Mac going back to sleep DURING one, and a `claude -p`
# killed mid-response still prints partial text — which run_claude() reads as
# success and marks `done`, losing that session's learning permanently
# (ENG-2026-08-20-016/-028/-030: archive/2026-08-17/8e5ec22a and
# 2026-08-18/1105ee74 are both `done` with learning_result ending "API Error:
# Your computer went to sleep mid-response" and zero engrams written).
# The sweep's own deadline is 4h, so it must survive an idle laptop that long.
# Re-exec under caffeinate rather than wrapping it in the plist: no launchctl
# reload needed, and it holds for a manual invocation too. The env guard is what
# stops the re-exec recursing.
if [ -z "${DATACORE_SWEEP_CAFFEINATED:-}" ] && command -v caffeinate >/dev/null 2>&1; then
  export DATACORE_SWEEP_CAFFEINATED=1
  exec caffeinate -i -s "$0" "$@"
fi

export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
LIB="$DATACORE_ROOT/.datacore/lib"
STATE="$HOME/.datacore/state"

# Resolve python by capability, not by path — the ledger job's hard-won rule.
# macOS ships 3.9, which cannot parse this codebase's PEP-604 unions.
PY=""
for c in "${DATACORE_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$c" ] || continue
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "FATAL: no python >= 3.10 found; the sweep cannot run." >&2
  exit 127
fi

mkdir -p "$STATE"
echo "=== $(date '+%F %T') session learning daily ==="
echo "python: $PY"

"$PY" "$LIB/session_archive.py" --backfill 2 --status pending > "$STATE/session-archive.log" 2>&1
echo "archive rc=$?"
tail -1 "$STATE/session-archive.log"

"$PY" "$LIB/session_learning_sweep.py" --backlog
sweep_rc=$?
echo "sweep rc=$sweep_rc"

# Report the queue after the run. A sweep that reports success while the queue
# grows is the failure mode worth catching — same reason ledger_ingest_org
# reports drift rather than silently importing it.
"$PY" "$LIB/session_learning_sweep.py" --status | tail -3

exit $sweep_rc
