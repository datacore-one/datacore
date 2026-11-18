#!/usr/bin/env bash
# One claim-loop tick for a resident: for each space, take in the others' work,
# claim what is addressed to this actor, run it, second-check what others
# finished, converge. One versioned script, so every resident runs the
# identical loop from its own scheduler.
#
#     ledger_claim_run.sh [<space-dir> [<actor> [limit]]]
#
# With no space: EVERY space of this installation that has a ledger. It used to
# be the system space only, while the delegation watcher reads all of them --
# so work delegated in any other space was reported as stuck and never run
# (audit 2026-11-15). With no actor: this machine's own actor (actor_identity)
# -- never a name of ours.
#
# Logs to $DATACORE_STATE/ledger-claim.log. Writes ONE status line to
# $DATACORE_STATE/ledger-claim-status.txt, which the host's contract reads:
#     OK claim <utc> spaces=<n>
#     FAIL claim <utc> <space>: <why>; ...
# FAIL means the loop could not do its job (a run stopped, an item needs a
# person and is refused every tick, the dispatcher crashed, a ledger log has a
# damaged line, the space's results could not be published). A delegated task
# failing its check is NOT a fail: that is the loop working.
# The box keeps its cron environment in cos.env; a host without it needs nothing.
set -uo pipefail
ROOT="${DATACORE_ROOT:-$HOME/Data}"
SPACE="${1:-}"; ACTOR="${2:-}"; LIMIT="${3:-2}"
LIB="$ROOT/.datacore/lib"

SPACES=()
if [ -n "$SPACE" ]; then
  SPACES=("$SPACE")
else
  while IFS= read -r _s; do
    [ -n "$_s" ] && SPACES+=("$_s")
  done < <(cd "$LIB" && DATACORE_ROOT="$ROOT" python3 -c '
import sys
from pathlib import Path
from spaces import discover_spaces
root = Path(sys.argv[1]).resolve()
for s in discover_spaces(root):
    if (s.path / ".datacore" / "events").is_dir():
        print(s.path)
' "$ROOT" 2>/dev/null)
  [ "${#SPACES[@]}" -gt 0 ] || { echo "ledger_claim_run: no space given and no space with a ledger under $ROOT" >&2; exit 2; }
fi
if [ -z "$ACTOR" ]; then
  ACTOR="$(cd "$LIB" && python3 -c 'import actor_identity; print(actor_identity.this_actor(strict=True))' 2>/dev/null)"
  [ -n "$ACTOR" ] || { echo "ledger_claim_run: no actor given and this machine has no actor identity" >&2; exit 2; }
fi

STATE="${DATACORE_STATE:-$HOME/.datacore/state}"
LOG="$STATE/ledger-claim.log"
STATUS="$STATE/ledger-claim-status.txt"
mkdir -p "$STATE"
# shellcheck disable=SC1091
[ -f "$LIB/cos_env.sh" ] && . "$LIB/cos_env.sh" >/dev/null 2>&1

PROBLEMS=""
note() { PROBLEMS="${PROBLEMS:+$PROBLEMS; }$1"; }

for SP in "${SPACES[@]}"; do
  NAME="$(basename "$SP")"
  OUT="$(mktemp)"
  {
    printf '=== %s %s@%s %s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ACTOR" "$(hostname -s)" "$NAME"
    # Take in the other hosts' work through converge, never a raw `git pull`: a
    # pull that meets a content conflict leaves a merge in progress, and every
    # later cycle then refuses the whole space (fleet week sim fault F8, SYN-9).
    # converge completes the merge around the conflicted file, files one task
    # for it, and names it on this line every run until a person settles it.
    # --actor: on a host in scheduled mode (DATACORE_PUSH_MODE) an agent's tick
    # receives here and its slot job publishes; in the default mode this pushes.
    DATACORE_ROOT="$ROOT" python3 "$LIB/ledger_transport.py" converge --line --space "$SP" --actor "$ACTOR" 2>&1 | tail -1
    # ANTHROPIC_API_KEY unset: with it set, claude -p bills the metered API
    # instead of the plan (the same rule cos_llm.sh applies).
    env -u ANTHROPIC_API_KEY DATACORE_ROOT="$ROOT" python3 "$LIB/ledger_claim.py" \
      --space "$SP" --actor "$ACTOR" --limit "$LIMIT" --execute 2>&1
    echo "claim-exit=$?"
    # THE SECOND CHECK (work another writer finished for the owner, checked
    # again by a writer that did not do it) is part of the dispatcher's own
    # run since 2026-11-16, so hosts that do not use this script make it too.
    DATACORE_ROOT="$ROOT" python3 "$LIB/ledger_transport.py" converge --line --space "$SP" --actor "$ACTOR" 2>&1 | tail -1
  } > "$OUT" 2>&1
  cat "$OUT" >> "$LOG"

  LINE="$(grep '^CLAIM-STATUS ' "$OUT" | tail -1)"
  RC="$(sed -n 's/^claim-exit=//p' "$OUT" | tail -1)"
  case "$LINE" in
    "CLAIM-STATUS $NAME OK"*) : ;;
    "CLAIM-STATUS $NAME ATTENTION "*) note "$NAME: ${LINE#CLAIM-STATUS $NAME ATTENTION }" ;;
    *) note "$NAME: the dispatcher printed no status (exit ${RC:-?}): $(grep -v '^===' "$OUT" | grep -v -- '-exit=' | tail -1 | cut -c1-160)" ;;
  esac
  if grep -q '^second check could not run' "$OUT"; then
    note "$NAME: $(grep -m1 '^second check could not run' "$OUT" | cut -c1-160)"
  fi
  # Results that never leave this host are not delivered. One agent's run ended
  # "converged but not published" on every tick for four days (2026-11-12..16)
  # and this status stayed OK. The LAST converge line is the publish.
  PUB="$(grep '^converge ' "$OUT" | tail -1)"
  case "$PUB" in
    *": FAIL "*) note "$NAME: not published: $(printf '%s' "${PUB#*: FAIL }" | sed 's/^[^A-Za-z]*//' | cut -c1-160)" ;;
  esac
  # A damaged line is skipped so one bad record never stops the space -- but
  # what it hid may be delegated work, so the run is not OK until it is looked at.
  if grep -q 'corrupt event log' "$OUT"; then
    note "$NAME: a ledger log has a damaged line (skipped): $(grep -m1 -o 'corrupt event log [^:]*' "$OUT" | sed 's#.*/\.datacore/##')"
  fi
  rm -f "$OUT"
done

NOW="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
if [ -n "$PROBLEMS" ]; then
  printf 'FAIL claim %s %s\n' "$NOW" "$PROBLEMS" > "$STATUS"
  printf 'FAIL claim %s %s\n' "$NOW" "$PROBLEMS" >> "$LOG"
  exit 1
fi
printf 'OK claim %s spaces=%s\n' "$NOW" "${#SPACES[@]}" > "$STATUS"
exit 0
