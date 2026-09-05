#!/usr/bin/env bash
# The dispatcher's cron tick on a satellite host (plur-claw runs it as Data):
# bring the space up to date, claim and execute up to N delegated items,
# publish the claims on this actor's own ref. Never rebase, never force.
#
# Until 2026-09-06 the tick on plur-claw was a root-owned copy of this script
# with the old /root layout, `git pull --rebase` and a python file that no
# longer existed, logging into a root-owned file the cron user could not
# write. It had failed silently on every tick since 2026-08-13. Paths come
# from the environment now, with the DIP-0044 layout as the default, and every
# tick prints one dated line so a job contract can see it ran.
set -uo pipefail
RUNNER="${DATACORE_RUNNER:-$HOME/.datacore/v2-runner}"
S="${DISPATCH_SPACE:-$HOME/spaces/5-plur}"
LIMIT="${DISPATCH_LIMIT:-2}"
PY="${DATACORE_PYTHON:-python3}"
ACTOR="$("$PY" "$RUNNER/.datacore/lib/actor_identity.py" 2>/dev/null | cut -d' ' -f1)"
[ -n "$ACTOR" ] || { echo "$(date -Is) dispatch rc=2 no declared actor (DIP-0044)"; exit 2; }
cd "$S" || { echo "$(date -Is) dispatch rc=2 space missing: $S"; exit 2; }
git pull --no-rebase -q 2>&1 | tail -2
"$PY" "$RUNNER/.datacore/lib/ledger_claim.py" --space "$S" --actor "$ACTOR" --limit "$LIMIT" --execute
rc=$?
git add .datacore/events/ 2>/dev/null
if ! git diff --cached --quiet 2>/dev/null; then
  git commit -q -m "ledger: $ACTOR claim/completion" 2>/dev/null
  git push -q origin "HEAD:refs/heads/ledger/$ACTOR" 2>&1 | tail -1
fi
echo "$(date -Is) dispatch rc=$rc actor=$ACTOR space=$(basename "$S")"
exit $rc
