#!/bin/bash
# Data's pull cycle: fetch delegations, work, publish claims.
#
# The git steps are not optional decoration. Without the pull Data never sees
# what winston delegated; without the push its claim and completion exist only
# on this box, and every other actor still believes the item is unclaimed.
set -uo pipefail
S=/root/spaces/5-plur
cd "$S" || exit 1
git pull --rebase -q 2>&1 | tail -2
/usr/bin/python3 /root/.datacore/v2-runner/.datacore/lib/ledger_dispatch.py \
  --space "$S" --actor data --limit 2 --execute
rc=$?
git add .datacore/events/ 2>/dev/null
# Nothing to commit is the normal case (no items claimed this tick).
git -c user.email=data@plur.ai -c user.name='Data (plur-claw)' \
  commit -q -m 'ledger: Data claim/completion' 2>/dev/null && \
  git push -q --force-with-lease origin HEAD:refs/heads/ledger/data 2>&1 | tail -1
exit $rc
