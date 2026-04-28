#!/bin/bash
# Venture heartbeat wrapper — Miles (Chief of Operations)
# Uses OAuth/subscription auth (NOT API key) to avoid burning credits.
# Claude Code finds credentials at ~/.claude/.credentials.json
export HOME=/home/gregor
export PATH=/usr/local/bin:/usr/bin:/bin

# Source nightshift env for non-Claude vars (TELEGRAM_BOT_TOKEN etc.)
# but UNSET ANTHROPIC_API_KEY so Claude uses subscription auth
set -a
source /home/gregor/config/nightshift.env
set +a
unset ANTHROPIC_API_KEY

# Miles identity for git commits
export GIT_AUTHOR_NAME="nightshift"
export GIT_AUTHOR_EMAIL="nightshift@datacore.one"
export GIT_COMMITTER_NAME="nightshift"
export GIT_COMMITTER_EMAIL="nightshift@datacore.one"

exec /usr/bin/python3 -u /home/gregor/Data/.datacore/modules/ventures/lib/venture_heartbeat.py --interval=900
