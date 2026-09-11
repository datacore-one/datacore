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
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime_shell.sh" || exit 2
datacore_runtime_init || exit $?
echo "python: $PY"

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
write_rc=$?
if [ "$write_rc" -ne 0 ]; then
  echo "checkpoint write rc=$write_rc; current backup not verified"
  exit "$write_rc"
fi
"$PY" "$LIB/ledger_checkpoint.py" verify > "$STATE/checkpoint-verify.log" 2>&1
verify_rc=$?
echo "ckpt   rc=$verify_rc"
tail -1 "$STATE/checkpoint-verify.log"

[ "$verify_rc" -eq 0 ] || exit "$verify_rc"
exit $check_rc
