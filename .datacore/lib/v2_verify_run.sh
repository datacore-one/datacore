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

source "$(dirname -- "${BASH_SOURCE[0]}")/runtime_shell.sh" || exit 2
datacore_runtime_init || exit $?
OUT="$STATE/v2-verify.log"
TMP="$(mktemp "$(dirname -- "$OUT")/.$(basename -- "$OUT").XXXXXX")" || exit 2

{
  echo "=== $(date -u '+%F %T') UTC — v2 verify on $(hostname -s) ==="
  # PYTHONUNBUFFERED: belt-and-suspenders with the in-code reconfigure
  # — the code does it too, but a wrapper env costs nothing and survives
  # an import-time crash before reconfigure() runs.
  PYTHONUNBUFFERED=1 \
    timeout 1800 "$PY" "$LIB/v2_verify.py"
  rc=$?
  if [ "$rc" = 124 ]; then
    echo "v2-verify: TIMED OUT after 30 min (timeout 1800)"
  else
    echo "v2-verify: exit $rc"
  fi
} > "$TMP" 2>&1

mv -f "$TMP" "$OUT" || { rm -f "$TMP"; exit 2; }
# Keep the process status directly; log text is never an exit-status source.

# Alert only on a real failure. The checklist prints "0 FAIL" when healthy, and
# an alert that fires on every run is one nobody reads — the same reason the
# drift gate is reported rather than failed.
if [ "${rc:-1}" != "0" ]; then
  ALERT="$DATACORE_ROOT/.datacore/modules/chief-of-staff/server/lib/cos_ledger_event.sh"
  [ -x "$ALERT" ] && "$ALERT" v2-verify failed "$(grep -c FAIL "$OUT") check(s) failing" || true
  echo "v2-verify FAILED — see $OUT" >&2
fi
exit "${rc:-1}"
