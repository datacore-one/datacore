#!/usr/bin/env bash
# Bring the k3s agent pods (Tris, Data) up to the published Datacore code, and
# put Tris's Hermes plugin copy in step with it.
#
#   ssh <box> 'bash -s' < .datacore/lib/hermes_plugin/fleet_refresh_pods.sh
#
# Why (2026-11-28): both pods' ~/.datacore/v2-runner sat 10 commits behind
# (since 2026-11-22) and Tris's ~/.hermes/plugins/datacore copy dated from
# 2026-09-07. Hermes loads that COPY, not the repo, so no plugin fix reached
# Tris; Data's OpenClaw plugin loads straight from v2-runner (no copy).
# Fast-forward only: a pod with local commits is reported, never merged here.
set -uo pipefail
K="sudo -n k3s kubectl -n firm"
pod() { $K get pods --no-headers | awk -v p="^$1-" '$3=="Running" && $1 ~ p {print $1; exit}'; }

for A in tris data; do
  P=$(pod "$A"); echo "== $A ($P)"
  [ -n "$P" ] || { echo "  no running pod"; continue; }
  $K exec "$P" -c gateway -- sh -c '
    cd ~/.datacore/v2-runner || exit 1
    git fetch -q origin && git pull --ff-only -q origin main \
      && echo "  v2-runner: $(git log -1 --format="%h %cd" --date=short), behind: $(git rev-list --count HEAD..origin/main)" \
      || echo "  v2-runner: NOT fast-forwardable (local commits: $(git rev-list --count origin/main..HEAD)); left as is"'
done

T=$(pod tris)
if [ -n "$T" ]; then
  echo "== tris plugin copy"
  $K exec "$T" -c gateway -- sh -c '
    S=~/.datacore/v2-runner/.datacore/lib/hermes_plugin; D=~/.hermes/plugins/datacore
    cp "$S/plugin.yaml" "$S/__init__.py" "$S/verify_loaded.py" "$D/" && rm -rf "$D/__pycache__" \
      && echo "  copied: $(grep -c _HERMES_RUNTIME_KEYS "$D/__init__.py") marker line(s)"
    pkill -f "^/opt/hermes-agent/venv/bin/python -m hermes_cli.main gateway run" && echo "  gateway stopped for restart"'
  sleep 30
  $K get pod "$T" --no-headers | awk '{print "  pod:", $2, $3, "restarts:", $4, $5, $6}'
  $K exec "$T" -c gateway -- sh -c '/opt/hermes-agent/venv/bin/python ~/.hermes/plugins/datacore/verify_loaded.py' | sed 's/^/  /'
fi
