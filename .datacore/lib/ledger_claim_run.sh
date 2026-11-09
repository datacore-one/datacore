#!/usr/bin/env bash
# One claim-loop tick for a resident: pull the space, claim what is addressed to
# this actor, run it, converge. The same three steps nightshift's
# ledger-claim.service performs for Miles, as one versioned script so a second
# resident (winston, 2026-09-22) runs the identical loop from cron.
#
#     ledger_claim_run.sh [<space-dir> [<actor> [limit]]]
#
# With no space, the install's system space (install.yaml roles.system); with no
# actor, this machine's own actor (actor_identity) -- never a name of ours.
# Logs to $DATACORE_STATE/ledger-claim.log, which the host's contract reads.
set -uo pipefail
ROOT="${DATACORE_ROOT:-$HOME/Data}"
SPACE="${1:-}"; ACTOR="${2:-}"; LIMIT="${3:-2}"
if [ -z "$SPACE" ]; then
  _sys="$(python3 "$ROOT/.datacore/lib/spaces.py" role system --root "$ROOT" 2>/dev/null | head -1)"
  [ -n "$_sys" ] || { echo "ledger_claim_run: no space given and no roles.system in install.yaml" >&2; exit 2; }
  SPACE="$ROOT/$_sys"
fi
if [ -z "$ACTOR" ]; then
  ACTOR="$(cd "$ROOT/.datacore/lib" && python3 -c 'import actor_identity; print(actor_identity.this_actor(strict=True))' 2>/dev/null)"
  [ -n "$ACTOR" ] || { echo "ledger_claim_run: no actor given and this machine has no actor identity" >&2; exit 2; }
fi
LOG="${DATACORE_STATE:-$HOME/.datacore/state}/ledger-claim.log"
mkdir -p "$(dirname "$LOG")"
# The box keeps its cron environment in cos.env; a host without it needs nothing.
[ -f "$ROOT/.datacore/lib/cos_env.sh" ] && . "$ROOT/.datacore/lib/cos_env.sh" >/dev/null 2>&1
{
  printf '=== %s %s@%s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ACTOR" "$(hostname -s)"
  # Take in the other hosts' work through converge, never a raw `git pull`: a
  # pull that meets a content conflict leaves a merge in progress, and every
  # later cycle then refuses the whole space (fleet week sim fault F8, SYN-9).
  # converge completes the merge around the conflicted file, files one task
  # for it, and names it on this line every run until a person settles it.
  DATACORE_ROOT="$ROOT" python3 "$ROOT/.datacore/lib/ledger_transport.py" converge --line --space "$SPACE" 2>&1 | tail -1
  # ANTHROPIC_API_KEY unset: with it set, claude -p bills the metered API
  # instead of the plan (the same rule cos_llm.sh applies).
  env -u ANTHROPIC_API_KEY DATACORE_ROOT="$ROOT" python3 "$ROOT/.datacore/lib/ledger_claim.py" \
    --space "$SPACE" --actor "$ACTOR" --limit "$LIMIT" --execute 2>&1
  DATACORE_ROOT="$ROOT" python3 "$ROOT/.datacore/lib/ledger_transport.py" converge --line --space "$SPACE" 2>&1 | tail -1
} >> "$LOG" 2>&1
