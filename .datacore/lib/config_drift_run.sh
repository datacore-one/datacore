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
# after its slot -- usually a lid-closed maintenance wake of a few seconds with
# the network half up, where one lost ssh call reads as fleet drift. Skip those
# and leave the previous log in place: the contract's freshness is measured in
# awake time, so a skipped maintenance wake costs nothing, and a real wake runs
# the probe for real. If the state cannot be read, it runs.
if "$PY" -c "import sys; sys.path.insert(0, '$LIB'); from jobs.awake import in_dark_wake; raise SystemExit(0 if in_dark_wake() else 1)" 2>/dev/null; then
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') skipped: maintenance wake (lid closed, nobody at it)" >> "$STATE/config-drift.skipped.log"
  exit 0
fi

exec "$PY" "$LIB/detectors/config_drift.py" \
    > "$STATE/config-drift.log" 2>&1
