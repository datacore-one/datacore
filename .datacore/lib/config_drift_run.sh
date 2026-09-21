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
# NOTHING SEEN IS NOT THE SAME AS NOTHING WRONG, and it is not an incident either.
#
# The hold used to require a dark wake. On 2026-09-20 at 21:28 the laptop was
# properly awake and simply had no route to winston or nightshift -- a train, a
# captive network, tailscale not yet up -- so the hold did not apply, two
# reachable-in-every-other-respect machines were written down as drift, and the
# contract paged. Both hosts answered on the first try the next morning.
#
# So the hold is now about what was LEARNED, not about which kind of wake it
# was: zero drift found and every finding merely unreachable means this run
# learned nothing, and a run that learned nothing must not overwrite the last
# one that did. Real drift is always written, awake or dark, network or not.
#
# This cannot hide a sustained problem, and that is what makes it safe: the
# artifact keeps its old mtime, max_age_hours is judged in AWAKE time
# (jobs/awake.py), and a fleet this machine genuinely cannot check for 26 waking
# hours goes stale and fails the contract on exactly those grounds.
if [ "$rc" -ne 0 ] && [ "${drift:-0}" = "0" ] && [ "${unreach:-0}" -gt 0 ] && [ -s "$OUT" ]; then
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') kept previous log: nothing learned, only unreachable ($summary)" >> "$STATE/config-drift.skipped.log"
  rm -f "$TMP"
  exit 0
fi
mv -f "$TMP" "$OUT"
exit "$rc"
