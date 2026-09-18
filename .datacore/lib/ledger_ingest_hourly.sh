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

echo "=== $(date '+%F %T') ledger ingest (hourly) ==="
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
"$PY" "$LIB/ledger_ingest_org.py" > "$tmp" 2>&1
ingest_rc=$?
mv -f "$tmp" "$out" || exit 2
echo "ingest rc=$ingest_rc"

exit $ingest_rc
