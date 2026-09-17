#!/bin/bash
# config-drift under launchd, not cron.
#
# The detector SSHes to four machines. Under cron on a laptop that fails in two
# ways that look identical in the log and are not:
#
#   1. cron does not run missed jobs, so a run scheduled while the Mac sleeps
#      simply never happens and the artifact ages out.
#   2. a run that lands while the Mac is waking has no usable agent socket, so
#      hermes reports unreachable and the contract goes red — while the very
#      next manual run passes. That fired repeatedly on 2026-08-12 and each
#      alert was a false one.
#
# launchd fires a missed StartCalendarInterval on wake, and the agent socket is
# resolved HERE at run time rather than baked into a crontab line, because its
# path changes per login session.
#
# The detector's own principle is unchanged and deliberately so: a machine that
# does not answer is reported, never skipped. This only removes the cases where
# the local machine, not the remote one, was the reason.
set -u
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime_shell.sh" || exit 2
datacore_runtime_init || exit $?
SOCK=$(launchctl getenv SSH_AUTH_SOCK 2>/dev/null || true)
[ -n "$SOCK" ] && export SSH_AUTH_SOCK="$SOCK"

# A laptop is not a server. launchd runs this coalesced job in the first wake
# after its slot -- often a lid-closed maintenance wake of a few seconds with
# the network half up, where an ssh call can drop. The detector already retries
# and names a lost connection "unreachable" rather than drift. What a dark wake
# can cause is exactly that one outcome, so only that one is held back: if the
# machine is in a dark wake AND every finding is "unreachable", the previous log
# stays in place (freshness is judged in awake time) and the skip is recorded.
# Real drift is always written, dark wake or not. An earlier version skipped
# the whole probe in any dark wake; dark wakes can last hours with the network
# fine, so that hid nothing but also checked nothing.
OUT="$STATE/config-drift.log"
TMP="$(mktemp "$STATE/.config-drift.XXXXXX")" || exit 2
"$PY" "$LIB/detectors/config_drift.py" > "$TMP" 2>&1
rc=$?
summary="$(tail -n 1 "$TMP")"
drift="$(printf '%s' "$summary" | sed -nE 's/.* ([0-9]+) with drift, ([0-9]+) unreachable.*/\1/p')"
unreach="$(printf '%s' "$summary" | sed -nE 's/.* ([0-9]+) with drift, ([0-9]+) unreachable.*/\2/p')"
if [ "$rc" -ne 0 ] && [ -n "$drift" ] && [ "$drift" = "$unreach" ] && [ "${unreach:-0}" -gt 0 ] \
   && "$PY" -c "import sys; sys.path.insert(0, '$LIB'); from jobs.awake import in_dark_wake; raise SystemExit(0 if in_dark_wake() else 1)" 2>/dev/null; then
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') kept previous log: dark wake, only unreachable ($summary)" >> "$STATE/config-drift.skipped.log"
  rm -f "$TMP"
  exit 0
fi
mv -f "$TMP" "$OUT"
exit "$rc"
