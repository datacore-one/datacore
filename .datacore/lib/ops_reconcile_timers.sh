#!/usr/bin/env bash
# ops_reconcile_timers.sh — first self-heal convergence job (datacore#55/#56).
#
# The 2026-08-16 incident: cos-token-refresh.timer was deleted from /etc (only
# the .service survived) and nothing restored it, so the Claude OAuth token in
# /etc/datacored.env stopped being refreshed. Detection existed (v2-verify ops
# check, morning verifier) but repair was manual — cos-server-setup.sh *would*
# have re-installed the timer (step 4/8) but it is a manual ritual. This is the
# first converger: it compares live state to the known-good config and repairs
# only a documented allow-list, loudly, to a log.
#
# Policy: ALLOW-LIST ONLY. This converges exactly one unit. Anything else
# (including failed services, unknown missing units) is NEVER touched here —
# unknown drift stays a human decision. Log line per action; silent when the
# unit is healthy, so a growing log IS the alert.
#
# Path convention (repo rule: no personal home paths in tracked files — the
# pre-commit path guardrail, ENG whitelist /home/deploy): paths are derived
# from $DATACORE_HOME or $HOME, with this box's home as the documented default.
# The live cos-server copy may carry the resolved literal; this source of truth
# is portable.
#
# The unit's source files live in the SPLIT: datacore-space.git
# (2-projects/datacore-app/daemon/ops/token-refresh/), not this repo. If the
# source is missing the script logs and aborts rather than fabricating a unit.
#
# Run as root (systemctl needs it). Wire into crontab (hourly, minute 20):
#   20 * * * * sudo <datacore_root>/.datacore/lib/ops_reconcile_timers.sh
# Safe to run any time (idempotent, ~1s when healthy).
set -uo pipefail

DATA=${DATACORE_HOME:-${1:-$HOME/Data}}
DAEMON=$DATA/2-datacore/2-projects/datacore-app/daemon
TR=$DAEMON/ops/token-refresh
UNIT=cos-token-refresh.timer
LOGDIR=$HOME/.datacore/cos
LOG=$LOGDIR/reconcile.log

mkdir -p "$LOGDIR"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }

# Healthy: unit file present, enabled, timer armed. Nothing to do.
if [ -e "/etc/systemd/system/$UNIT" ] \
   && systemctl is-enabled --quiet "$UNIT" \
   && systemctl is-active --quiet "$UNIT"; then
  exit 0
fi

log "DRIFT $UNIT: file=$( [ -e "/etc/systemd/system/$UNIT" ] && echo present || echo missing ) enabled=$(systemctl is-enabled "$UNIT" 2>/dev/null) active=$(systemctl is-active "$UNIT" 2>/dev/null)"

# Allow-listed repair: re-link from the canonical source, reload, re-arm.
for src in "$TR/cos-token-refresh.service" "$TR/cos-token-refresh.timer"; do
  if [ ! -f "$src" ]; then
    log "ABORT source missing: $src — not fabricating a unit; human needed"
    exit 1
  fi
done

ln -sf "$TR/cos-token-refresh.service" /etc/systemd/system/cos-token-refresh.service
ln -sf "$TR/cos-token-refresh.timer"   /etc/systemd/system/cos-token-refresh.timer
systemctl daemon-reload
systemctl enable --now "$UNIT" >/dev/null 2>&1

if systemctl is-active --quiet "$UNIT"; then
  log "REPAIRED $UNIT re-armed (active, next: $(systemctl list-timers "$UNIT" --no-pager --plain 2>/dev/null | sed -n 2p))"
else
  log "FAILED $UNIT still inactive after re-arm — escalating to a human"
  exit 1
fi
exit 0
