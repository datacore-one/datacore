#!/bin/bash
# Run the v2 checklist unattended and leave an artifact behind.
#
# Every failure found on 2026-08-13 had existed for hours or days before anyone
# looked: the nightly ledger cycle dead since ownership moved to Linux,
# cos_github pointing at a path that does not exist, github-triage failed for
# three days, two forked logs. None of them were hard to detect — nothing was
# detecting.
#
# A checklist only ever run by hand is one that mostly is not run. This exists
# so the question "is v2 still good?" is asked on a schedule rather than when
# someone happens to wonder.
#
# Writes ONE artifact, whose freshness AND content are both contracted in
# jobs/manifest.yaml — so a run that stops happening is caught by the same
# mechanism as a run that fails. A silent absence is the failure mode that hid
# everything above.
set -uo pipefail

export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
LIB="$DATACORE_ROOT/.datacore/lib"
STATE="$HOME/.datacore/state"
OUT="$STATE/v2-verify.log"
mkdir -p "$STATE"

# Same capability-based resolution as ledger_daily: a hardcoded interpreter is
# what killed the nightly cycle when it moved to a different OS.
PY=""
for c in "${DATACORE_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$c" ] || continue
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "v2-verify: no python >= 3.10" > "$OUT"; exit 127; }

{
  echo "=== $(date -u '+%F %T') UTC — v2 verify on $(hostname -s) ==="
  "$PY" "$LIB/v2_verify.py"
  rc=$?
  echo "v2-verify: exit $rc"
} > "$OUT" 2>&1
rc=$(grep -oE 'exit [0-9]+' "$OUT" | tail -1 | awk '{print $2}')

# Alert only on a real failure. The checklist prints "0 FAIL" when healthy, and
# an alert that fires on every run is one nobody reads — the same reason the
# drift gate is reported rather than failed.
if [ "${rc:-1}" != "0" ]; then
  ALERT="$DATACORE_ROOT/.datacore/modules/chief-of-staff/server/lib/cos_ledger_event.sh"
  [ -x "$ALERT" ] && "$ALERT" v2-verify failed "$(grep -c FAIL "$OUT") check(s) failing" || true
  echo "v2-verify FAILED — see $OUT" >&2
fi
exit "${rc:-1}"
