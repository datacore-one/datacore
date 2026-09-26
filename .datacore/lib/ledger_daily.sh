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

# EVERY SPACE'S HISTORY, EVERY DAY (OPS-3, audit C8). The daily "ledger
# verify" ran `ledger_cli.py verify --space ~/Data`, which reads only the
# root's gitignored telemetry dir ("OK 2 files 1734 events") -- the ~82k space
# events were never checked, so the alert was green by construction. Verify
# each space that carries an event log, discovered exactly as v2_verify.spaces()
# does, and report each one. Any failure fails the job.
#
# ledger-verify.log is what the manifest contract reads with `^OK ` in
# MULTILINE mode, so per-space lines are indented and only the LAST line may
# start with OK -- and only when every space verified.
verify_out="$STATE/ledger-verify.log"
verify_tmp="$(mktemp "$verify_out.XXXXXX")" || exit 2
verify_rc=0
spaces_list="$("$PY" -c 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from spaces import discover_spaces
for s in discover_spaces(Path(sys.argv[2])):
    if (s.path / ".datacore" / "events").is_dir():
        print(s.path)' "$LIB" "$DATACORE_ROOT" 2>"$verify_tmp.err")"
disc_rc=$?
n_ok=0; n_bad=0
if [ "$disc_rc" -ne 0 ]; then
  { echo "  discovery: $(tail -1 "$verify_tmp.err" 2>/dev/null)"
    echo "FAIL could not discover the spaces under $DATACORE_ROOT (rc=$disc_rc)"; } > "$verify_tmp"
  verify_rc=1
elif [ -z "$spaces_list" ]; then
  # Could-not-tell, never a pass: no line starts with OK, so the manifest
  # contract stays red, but an installation with no ledger yet is not a crash.
  echo "NONE no space carries an event log under $DATACORE_ROOT" > "$verify_tmp"
else
  while IFS= read -r sp; do
    [ -n "$sp" ] || continue
    name="$(basename "$sp")"
    one="$("$PY" "$LIB/ledger_cli.py" verify --space "$sp" 2>&1)"
    if [ $? -eq 0 ]; then
      n_ok=$((n_ok + 1))
      echo "  $name: OK $(printf '%s\n' "$one" | grep -v '^[[:space:]]*$' | tail -1 | sed 's/^OK //')" >> "$verify_tmp"
    else
      n_bad=$((n_bad + 1))
      echo "  $name: FAIL" >> "$verify_tmp"
      printf '%s\n' "$one" | head -20 | sed 's/^/    /' >> "$verify_tmp"
    fi
  done <<< "$spaces_list"
  if [ "$n_bad" -eq 0 ]; then
    echo "OK $n_ok space(s) verified" >> "$verify_tmp"
  else
    echo "FAIL $n_bad of $((n_ok + n_bad)) space(s) failed verification" >> "$verify_tmp"
    verify_rc=1
  fi
fi
rm -f "$verify_tmp.err"
mv -f "$verify_tmp" "$verify_out" || exit 2
echo "verify rc=$verify_rc"
cat "$verify_out"

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
ckpt_rc=$?
echo "ckpt   rc=$ckpt_rc"
tail -1 "$STATE/checkpoint-verify.log"

[ "$ckpt_rc" -eq 0 ] || exit "$ckpt_rc"
[ "$check_rc" -eq 0 ] || exit "$check_rc"
exit "$verify_rc"
