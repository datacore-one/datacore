#!/bin/bash
# Daily ledger verification: shadow_check + checkpoint (ingest is separate).
#
# INGEST IS NOT HERE. As of 2026-08-16, ingest was split into
# ledger_ingest_hourly.sh, which runs every hour on winston. This script runs
# the heavier verification passes once daily at 05:35, after the 05:00 hourly
# ingest has already completed.
#
# ORDER IS STILL LOAD-BEARING. The drift check compares org against the
# ledger's projection. It must run AFTER ingest — not before, not in place of.
# 05:35 is safely after the :00-past-the-hour ingest at 05:00. Do not move
# this job earlier than 05:01 without coordinating with the hourly schedule.
#
# WHY NOT CRON ON THE LAPTOP. These were cron entries on the Mac at 07:40 and
# 07:50. On 2026-08-12 the Mac was asleep through that window and macOS cron
# does not catch up missed runs. ledger_daily.sh now runs on winston (always-on
# Linux) via cron, which does not have a wake constraint.
#
# SERVER. Runs on winston via cron:
#   35 5 * * * DATACORE_ROOT=/home/deploy/Data /home/deploy/Data/.datacore/lib/ledger_daily.sh >> /home/deploy/.datacore/state/ledger-daily-cron.log 2>&1
set -u
export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
LIB="$DATACORE_ROOT/.datacore/lib"
STATE="$HOME/.datacore/state"
# RESOLVE PYTHON BY CAPABILITY, NOT BY PATH.
#
# This was hardcoded to /opt/homebrew/bin/python3 — correct on the Mac, absent
# everywhere else. Ledger ownership then moved to the always-on box, which is
# Linux, and the entire nightly cycle failed with rc=127 (command not found)
# every night: no ingest, no drift check, no checkpoint. It reported failure
# faithfully into a log nobody read.
#
# Still NOT plain `python3`: macOS ships 3.9, which cannot import the ledger at
# all (PEP-604 unions at module level). So test candidates and take the first
# that clears 3.10 — the same rule the CLI and MCP already apply.
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
  echo "FATAL: no python >= 3.10 found; the ledger cannot be loaded." >&2
  exit 127
fi
echo "python: $PY"
mkdir -p "$STATE"

echo "=== $(date '+%F %T') ledger daily (verification) ==="

# Run the check even if the most recent ingest had a non-zero exit: its result
# is still the truth about drift, and suppressing it would hide the consequence
# of the ingest failure.
"$PY" "$LIB/shadow_check.py"       > "$STATE/shadow-check.log" 2>&1
check_rc=$?
echo "drift  rc=$check_rc"
tail -2 "$STATE/shadow-check.log"

# Checkpoint LAST, from a ledger that has just been reconciled, then prove it
# restores. Writing one is cheap; the verify is the part with value — it
# rebuilds each checkpoint in a throwaway space and compares item by item, so
# "could we re-genesis from this?" is answered continuously rather than
# discovered during the incident that needs it.
"$PY" "$LIB/ledger_checkpoint.py" write  > "$STATE/checkpoint-write.log" 2>&1
"$PY" "$LIB/ledger_checkpoint.py" verify > "$STATE/checkpoint-verify.log" 2>&1
echo "ckpt   rc=$?"
tail -1 "$STATE/checkpoint-verify.log"

exit $check_rc
