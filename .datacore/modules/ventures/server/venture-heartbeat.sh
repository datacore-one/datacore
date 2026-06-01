#!/bin/bash
# Venture heartbeat wrapper — Miles (Chief of Operations)
# Uses OAuth/subscription auth (NOT API key) to avoid burning credits.
# Claude Code finds credentials at ~/.claude/.credentials.json
export HOME=/home/deploy
export PATH=/usr/local/bin:/usr/bin:/bin

# Source nightshift env for non-Claude vars (TELEGRAM_BOT_TOKEN etc.)
# but UNSET ANTHROPIC_API_KEY so Claude uses subscription auth
set -a
source "$HOME/Data/.datacore/env/.env"
set +a
unset ANTHROPIC_API_KEY

# Miles identity for git commits (per ENG-2026-0511-009 — supersedes earlier
# "nightshift infrastructure identity" pattern; meeting decision 2026-05-11
# routes ALL heartbeat operations through Miles for consistent attribution)
export GIT_AUTHOR_NAME="Miles"
export GIT_AUTHOR_EMAIL="gregor+miles@datafund.io"
export GIT_COMMITTER_NAME="Miles"
export GIT_COMMITTER_EMAIL="gregor+miles@datafund.io"

exec /usr/bin/python3 -u "$HOME/Data/.datacore/modules/ventures/lib/venture_heartbeat.py" --interval=1800
