#!/bin/bash
# Phase 1 cycle (DIP-0046): ingest -> converge -> project, hourly on every host.
#   ingest    what writers put into org files since the last cycle -> ledger
#   converge  this host's ledger logs with everyone else's (git transport)
#   project   for spaces in Phase 1 only: org/next_actions.org <- ledger
# Order is the whole point: projecting before ingesting loses a hand edit.
set -u
export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
# The scripts come from THIS checkout (a runner worktree on main is fine); only
# the data root is DATACORE_ROOT. A host whose ~/Data sits on someone's feature
# branch still runs current tooling against its own data.
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$HOME/.datacore/state"; mkdir -p "$STATE"
PY=""
for c in "${DATACORE_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$c" ] || continue; command -v "$c" >/dev/null 2>&1 || continue
  "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null && { PY="$c"; break; }
done
[ -n "$PY" ] || { echo "FATAL: no python >= 3.10"; exit 127; }
cd "$DATACORE_ROOT" || exit 2
echo "=== $(date -u '+%F %H:%MZ') phase-1 cycle ==="
PHASE1=$(for d in "$DATACORE_ROOT"/[0-9]-*; do [ "$(cat "$d/.datacore/ledger-phase" 2>/dev/null | tr -d '[:space:]')" = "1" ] && basename "$d"; done)
if [ -z "$PHASE1" ]; then echo "no space in Phase 1; nothing to do"; exit 0; fi
"$PY" "$LIB/ledger_ingest_org.py" --root "$DATACORE_ROOT" > "$STATE/phase1-ingest.log" 2>&1; echo "ingest  rc=$? $(tail -1 "$STATE/phase1-ingest.log" | cut -c1-100)"
rc=0
for s in $PHASE1; do
  "$PY" "$LIB/ledger_transport.py" converge --space "$s" > "$STATE/phase1-converge-$s.log" 2>&1 || { echo "converge $s: $(grep -o '"reason": "[^"]*"' "$STATE/phase1-converge-$s.log" | head -1)"; rc=1; }
done
"$PY" "$LIB/ledger_project_org.py" --all 2>&1 | grep -v "authored" ; echo "project rc=$?"
exit $rc
