#!/usr/bin/env bash
# One claim-loop tick for a resident: pull the space, claim what is addressed to
# this actor, run it, converge. The same three steps nightshift's
# ledger-claim.service performs for Miles, as one versioned script so a second
# resident (winston, 2026-09-22) runs the identical loop from cron.
#
#     ledger_claim_run.sh <space-dir> <actor> [limit]
#
# Logs to $DATACORE_STATE/ledger-claim.log, which the host's contract reads.
set -uo pipefail
SPACE="$1"; ACTOR="$2"; LIMIT="${3:-2}"
ROOT="${DATACORE_ROOT:-$HOME/Data}"
LOG="${DATACORE_STATE:-$HOME/.datacore/state}/ledger-claim.log"
mkdir -p "$(dirname "$LOG")"
# The box keeps its cron environment in cos.env; a host without it needs nothing.
[ -f "$ROOT/.datacore/lib/cos_env.sh" ] && . "$ROOT/.datacore/lib/cos_env.sh" >/dev/null 2>&1
{
  printf '=== %s %s@%s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ACTOR" "$(hostname -s)"
  git -C "$SPACE" pull -q --no-rebase origin main 2>&1 | tail -2
  # ANTHROPIC_API_KEY unset: with it set, claude -p bills the metered API
  # instead of the plan (the same rule cos_llm.sh applies).
  env -u ANTHROPIC_API_KEY DATACORE_ROOT="$ROOT" python3 "$ROOT/.datacore/lib/ledger_claim.py" \
    --space "$SPACE" --actor "$ACTOR" --limit "$LIMIT" --execute 2>&1
  DATACORE_ROOT="$ROOT" python3 "$ROOT/.datacore/lib/ledger_transport.py" converge --space "$SPACE" 2>&1 | tail -1
} >> "$LOG" 2>&1
