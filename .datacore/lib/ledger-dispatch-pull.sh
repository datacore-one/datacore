#!/bin/bash
# Data's pull cycle: fetch delegations, work, publish claims.
#
# The git steps are not optional decoration. Without the pull Data never sees
# what winston delegated; without the push its claim and completion exist only
# on this box, and every other actor still believes the item is unclaimed.
set -uo pipefail
S=/root/spaces/5-plur
cd "$S" || exit 1
# MERGE, NEVER REBASE (DIP-0046). This box publishes its claims to its own
# branch (refs/heads/ledger/data, below) rather than to main, so a rebase here
# rewrites exactly the commits it is about to push. When that push is rejected
# the claims survive only under hashes no other actor has ever seen, and the
# item still reads as unclaimed everywhere else — the failure this script's
# own header warns about.
#
# Merge is also simply correct for the payload: per-writer event logs are
# disjoint files, so receiving another actor's log is a union, not a conflict.
git pull --no-rebase -q 2>&1 | tail -2
/usr/bin/python3 /root/.datacore/v2-runner/.datacore/lib/ledger_dispatch.py \
  --space "$S" --actor data --limit 2 --execute
rc=$?
git add .datacore/events/ 2>/dev/null
# Nothing to commit is the normal case (no items claimed this tick).
git -c user.email=data@plur.ai -c user.name='Data (plur-claw)' \
  commit -q -m 'ledger: Data claim/completion' 2>/dev/null && \
  git push -q --force-with-lease origin HEAD:refs/heads/ledger/data 2>&1 | tail -1
exit $rc
