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
S="${DISPATCH_SPACE:-${DATACORE_ATTEST_SPACE:-$HOME/spaces/5-plur}}"
LIMIT="${DISPATCH_LIMIT:-2}"
PY="${DATACORE_PYTHON:-python3}"
# THE REGISTRY IS NOT IN THE RUNNER CHECKOUT AND NEVER WILL BE.
# `registry/principals.yaml` is gitignored on purpose -- it is the private
# overlay -- so a satellite running from ~/.datacore/v2-runner finds no
# principals at all unless it is told where the data root is. Without this,
# `actor_identity.REGISTRY_DIR` falls back to the runner directory, every
# writer resolves to no principal, and `claim_gate` refuses each one as
# "unregistered writer 'data' -- declare it in registry/principals.yaml"
# about a writer that IS declared, two directories away.
#
# Latent rather than loud: the spaces this tick watches held only
# org-mirrored tasks, so the claim path was never reached and the log shows
# zero such refusals. The 2026-09-18 delegation exercise put the first real
# delegation in front of it and every claim was refused. hermes's other crons
# already pass DATACORE_ROOT for exactly this reason; this one did not.
export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
# The host's declarations travel with the tick: executor, agent name, attest space.
if [ -f "$HOME/.datacore/identity.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in DATACORE_EXECUTOR|OPENCLAW_AGENT|DATACORE_ATTEST_SPACE|DATACORE_LEDGER_SIGN) export "$k=$(printf '%s' "$v" | sed -E "s/^['\"]//; s/['\"]$//")";; esac
  done < <(grep -E '^(DATACORE_EXECUTOR|OPENCLAW_AGENT|DATACORE_ATTEST_SPACE|DATACORE_LEDGER_SIGN)=' "$HOME/.datacore/identity.env")
fi
ACTOR="$("$PY" "$RUNNER/.datacore/lib/actor_identity.py" 2>/dev/null | cut -d' ' -f1)"
[ -n "$ACTOR" ] || { echo "$(date -Is) dispatch rc=2 no declared actor (DIP-0044)"; exit 2; }
cd "$S" || { echo "$(date -Is) dispatch rc=2 space missing: $S"; exit 2; }
# RECEIVE THROUGH THE TRANSPORT, not `git pull`. converge is where the rules
# live: it refuses to autosave over a half-finished merge, never stages a
# submodule pointer, folds every origin/ledger/* ref back into the branch, and
# tells offline apart from a rejected key. A bare pull here had none of that,
# and this tick is the one that runs unattended on a satellite host.
"$PY" "$RUNNER/.datacore/lib/ledger_transport.py" converge --space "$S" >/dev/null 2>&1 \
  || echo "$(date -Is) dispatch converge-in: could not receive; claiming against the copy on disk"
"$PY" "$RUNNER/.datacore/lib/ledger_claim.py" --space "$S" --actor "$ACTOR" --limit "$LIMIT" --execute
rc=$?
# STAGE THIS ACTOR'S OWN LOGS AND NOTHING ELSE. `git add .datacore/events/`
# staged every writer's file, so a log this host merely received -- Tris's,
# Winston's -- was committed and pushed under this actor's ref. That is the
# exact failure DIP-0044 authorship exists to prevent, and it happened twice on
# hermes in September before the transport grew `foreign_writer_logs`. This
# path bypassed that guard by never going through the transport at all. The
# glob also takes `<actor>-run-YYYY-MM-DD.jsonl`, which is the same writer on a
# run branch, and nothing else.
#
# Staged in two calls, not one. An unmatched glob stays literal, and `git add`
# fails the WHOLE invocation on a pathspec that matches nothing -- so on the
# ordinary tick, where no run-scoped log exists, one combined call staged
# nothing at all and the tick published none of its own work either.
git add ".datacore/events/$ACTOR.jsonl" 2>/dev/null
for run_log in ".datacore/events/$ACTOR-run-"*.jsonl; do
  [ -f "$run_log" ] && git add "$run_log" 2>/dev/null
done
if ! git diff --cached --quiet 2>/dev/null; then
  git commit -q -m "ledger: $ACTOR claim/completion" 2>/dev/null
  # Published on this writer's OWN ref: only this writer pushes there, so the
  # push cannot race. converge merges origin/ledger/* back into the branch on
  # every host, which is how these claims reach main.
  git push -q origin "HEAD:refs/heads/ledger/$ACTOR" 2>&1 | tail -1
fi
echo "$(date -Is) dispatch rc=$rc actor=$ACTOR space=$(basename "$S")"
exit $rc
