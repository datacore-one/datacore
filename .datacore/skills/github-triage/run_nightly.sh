#!/usr/bin/env bash
# Nightly GitHub triage (Miles, nightshift host). Replaces triage_github.sh.
# Owner decisions 2026-09-29: the 09-11 triage skill; public repos and the
# enterprise repo in SEPARATE runs; incremental Mon-Sat, full sweep Sunday;
# claude -p on the subscription; read-only on GitHub (gh shim first on PATH);
# outputs committed to the private personal space, board rendered on the Mac.
# Host-agnostic: DATA_DIR, CLAUDE_BIN and TRIAGE_NO_PUSH come from the environment.
set -uo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${DATA_DIR:-$HOME/Data}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
# The owner whose queue this is. On the nightshift host gh is logged in as the
# bot account, so the owner is never inferred from `gh api user`.
OWNER="${TRIAGE_OWNER:-${GITHUB_TRIAGE_USERNAME:-plur9}}"
DATE="$(date -u +%F)"
[ "${1:-}" = "--date" ] && DATE="$2"
DOW="$(python3 -c "import datetime,sys;print(datetime.date.fromisoformat(sys.argv[1]).isoweekday())" "$DATE")"
MODE=incremental; [ "$DOW" = "7" ] && MODE=full
OUT_REL="notes/github-triage"
PERSONAL="$DATA_DIR/0-personal"
LOG_DIR="${TRIAGE_LOG_DIR:-$HOME/.datacore/state/github-triage}"
mkdir -p "$LOG_DIR" "$PERSONAL/$OUT_REL"
export PATH="$SKILL/bin:$PATH"      # read-only gh, enforced
export DATA_DIR
rc_all=0
files=()
for SCOPE in public enterprise; do
  log="$LOG_DIR/$DATE-$SCOPE.log"
  echo "[github-triage] $SCOPE $MODE $DATE start $(date -u +%T)" | tee -a "$log"
  prompt="Use the github-triage skill: read .datacore/skills/github-triage/SKILL.md and follow it exactly. SCOPE=$SCOPE MODE=$MODE DATE=$DATE OWNER=$OWNER. Work only in $DATA_DIR."
  ( cd "$DATA_DIR" && printf '%s' "$prompt" | timeout "${TRIAGE_TIMEOUT:-5400}" "$CLAUDE_BIN" -p \
      --dangerously-skip-permissions --disallowedTools Task,Agent ) >> "$log" 2>&1
  rc=$?
  board="$PERSONAL/$OUT_REL/$DATE-$SCOPE.board.json"
  report="$PERSONAL/$OUT_REL/$DATE-$SCOPE.md"
  if [ -f "$board" ] && python3 "$SKILL/triage_board.py" validate "$board" >> "$log" 2>&1; then
    echo "[github-triage] $SCOPE board valid" | tee -a "$log"
  else
    echo "[github-triage] $SCOPE FAILED (claude rc=$rc; board missing or invalid)" | tee -a "$log"; rc_all=1
  fi
  for f in "$report" "$board"; do [ -f "$f" ] && files+=("$OUT_REL/$(basename "$f")"); done
done
if [ "${#files[@]}" -gt 0 ]; then
  git -C "$PERSONAL" add -- "${files[@]}"
  git -C "$PERSONAL" commit -q -m "github-triage: $DATE $MODE (public + enterprise)" -- "${files[@]}" \
    || { echo "[github-triage] commit failed"; rc_all=1; }
  if [ -z "${TRIAGE_NO_PUSH:-}" ]; then
    ( cd "$DATA_DIR" && python3 .datacore/lib/ledger_transport.py sync --repo 0-personal ) >> "$LOG_DIR/$DATE-push.log" 2>&1 \
      || { echo "[github-triage] push/converge failed (see $DATE-push.log)"; rc_all=1; }
  fi
fi
echo "[github-triage] done $DATE rc=$rc_all"
exit $rc_all
