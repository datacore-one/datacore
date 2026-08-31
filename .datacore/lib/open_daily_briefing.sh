#!/bin/bash
# Open today's briefing at the start of the working day.
# Syncs first: the briefing is generated overnight on the nightshift server and only
# reaches this machine through the ledger transport. Opening without syncing shows
# yesterday's file, which is worse than showing nothing.
set -u
export PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
DATA="$HOME/Data"
LOG="$HOME/.datacore/state/daily-briefing-open.log"
mkdir -p "$(dirname "$LOG")"
# /usr/bin/python3 is too old for the transport (str | None needs 3.10+); pick a modern one.
PY="$(command -v python3)"
# date_utils prints "YYYY-MM-DD Day" — take the first field only.
TODAY="$("$PY" "$DATA/.datacore/lib/date_utils.py" today 2>/dev/null | tail -1 | awk '{print $1}')"
case "$TODAY" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) TODAY="$(date +%Y-%m-%d)" ;; esac
{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S') opening briefing for $TODAY (python: $PY)"
  timeout 240 "$PY" "$DATA/.datacore/lib/ledger_transport.py" converge --space "$DATA/0-personal" 2>&1 | tail -2
  J="$DATA/0-personal/notes/journals/$TODAY.md"
  if [ -f "$J" ]; then
    grep -q "## Daily Briefing" "$J" && echo "briefing present" || echo "WARN: journal exists but has no ## Daily Briefing section"
    /usr/bin/open "$J" && echo "opened $J"
  else
    echo "WARN: no journal for $TODAY — nightshift may not have run"
    /usr/bin/open "$DATA/0-personal/notes/journals/" && echo "opened journals folder instead"
  fi
} >> "$LOG" 2>&1
