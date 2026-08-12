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
export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
SOCK=$(launchctl getenv SSH_AUTH_SOCK 2>/dev/null || true)
[ -n "$SOCK" ] && export SSH_AUTH_SOCK="$SOCK"

exec /opt/homebrew/bin/python3 "$DATACORE_ROOT/.datacore/lib/detectors/config_drift.py" \
    > "$HOME/.datacore/state/config-drift.log" 2>&1
