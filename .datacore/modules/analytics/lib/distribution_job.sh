#!/usr/bin/env bash
# Daily distribution snapshot (distribution plan, owner 2026-11-17).
#
#   distribution_job.sh <space_dir> <config path relative to the space>
#
# 1. fast-forward the space (never stash, rebase or reset: a blocked pull fails the job)
# 2. run the collector against the config
# 3. commit only the metrics folder, as the running agent (GIT_AUTHOR_* from the caller)
# 4. push
# The module holds no user data: the space and config come from the caller.
set -euo pipefail

SPACE="${1:?usage: distribution_job.sh <space_dir> <config_relpath>}"
CONF_REL="${2:?usage: distribution_job.sh <space_dir> <config_relpath>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR="${COLLECTOR:-$HERE/distribution_collector.py}"
METRICS_REL="$(dirname "$CONF_REL")"

git -C "$SPACE" pull -q --ff-only
python3 "$COLLECTOR" --config "$SPACE/$CONF_REL"

git -C "$SPACE" add -- "$METRICS_REL"
if git -C "$SPACE" diff --cached --quiet -- "$METRICS_REL"; then
  echo "distribution_job: no new snapshot to commit"
  exit 0
fi
git -C "$SPACE" commit -q -m "metrics: daily distribution snapshot $(date +%F)" -- "$METRICS_REL"
git -C "$SPACE" push -q
echo "distribution_job: snapshot committed and pushed"
