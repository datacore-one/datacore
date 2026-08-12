#!/bin/bash
# Daily ledger reconciliation: ingest org -> ledger, THEN check the projection.
#
# One job, not two cron lines, for two reasons.
#
# ORDER IS LOAD-BEARING. The drift check compares org against the ledger's
# projection, so every task captured since the last ingest reads as drift.
# Running the check first reports a healthy system as dirty on any day a task
# was captured — and since DIP-0046 F2 gates the Phase 1 flip on 14 consecutive
# clean days, that does not merely add noise, it makes the gate unreachable.
#
# LAUNCHD, NOT CRON. These were cron entries at 07:40 and 07:50. On 2026-08-12
# the Mac was asleep through that window (full wake at 09:14) and macOS cron
# does not catch up missed runs, so neither fired — while the hourly detectors
# either side of the gap did, which is exactly the pattern that makes a hole
# look like health. launchd runs a missed StartCalendarInterval job on wake.
#
# A laptop sleeping through 07:40 is the normal case, not the exception. Any
# once-daily job scheduled here via cron is a job that mostly does not run.
set -u
export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
LIB="$DATACORE_ROOT/.datacore/lib"
STATE="$HOME/.datacore/state"
PY=/opt/homebrew/bin/python3      # NOT /usr/bin/python3: macOS ships 3.9,
                                  # which lacks yaml and PEP-604 unions.
mkdir -p "$STATE"

echo "=== $(date '+%F %T') ledger daily ==="
"$PY" "$LIB/ledger_ingest_org.py"  > "$STATE/ledger-ingest.log" 2>&1
ingest_rc=$?
echo "ingest rc=$ingest_rc"

# Run the check even if ingest failed: its result is still the truth about
# drift, and suppressing it would hide the consequence of the failure.
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

exit $(( ingest_rc != 0 ? ingest_rc : check_rc ))
