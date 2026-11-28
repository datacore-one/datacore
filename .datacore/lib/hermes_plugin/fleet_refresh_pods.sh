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

# Winston runs from ~/Data, which carries the pod's own commits: merge, never
# force; a conflict is aborted and reported.
W=$(pod winston)
if [ -n "$W" ]; then
  echo "== winston ($W)"
  $K exec "$W" -c gateway -- sh -c '
    cd ~/Data && git fetch -q origin \
      && { git pull --no-rebase --no-edit -q origin main \
           && echo "  ~/Data: $(git log -1 --format="%h") behind: $(git rev-list --count HEAD..origin/main)" \
           || { git merge --abort 2>/dev/null; echo "  ~/Data: merge conflict, aborted; left as is"; }; }'
fi

# Hermes agents load a COPY of the plugin: refresh it, restart the gateway,
# check what loads and that the filing tools declare their fields.
for A in winston:Data tris:.datacore/v2-runner; do
  N=${A%%:*}; SRC=${A#*:}; P=$(pod "$N")
  [ -n "$P" ] || continue
  echo "== $N plugin copy"
  $K exec "$P" -c gateway -- sh -c "
    S=\$HOME/$SRC/.datacore/lib/hermes_plugin; D=\$HOME/.hermes/plugins/datacore
    cp \"\$S/plugin.yaml\" \"\$S/__init__.py\" \"\$S/verify_loaded.py\" \"\$D/\" && rm -rf \"\$D/__pycache__\" && echo '  copied'
    pkill -f '^/opt/hermes-agent/venv/bin/python -m hermes_cli.main gateway run' && echo '  gateway stopped for restart'"
done
sleep 30
for N in winston tris; do
  P=$(pod "$N"); [ -n "$P" ] || continue
  echo "== $N check"
  $K get pod "$P" --no-headers | awk '{print "  pod:", $2, $3, "restarts:", $4, $5, $6}'
  $K exec "$P" -c gateway -- sh -c '
    PY=/opt/hermes-agent/venv/bin/python
    $PY ~/.hermes/plugins/datacore/verify_loaded.py
    cd ~/.hermes/plugins && $PY -c "
import sys; sys.path.insert(0, \".\"); import datacore as d
got = {}
class C:
    def register_hook(self, *a): pass
    def register_tool(self, **k): got[k[\"name\"]] = k[\"schema\"]
d.register(C())
for n in (\"datacore_delegate\", \"datacore_ledger_append\"):
    print(\"  \" + n + \" fields:\", \",\".join(sorted(got[n].get(\"parameters\", {}).get(\"properties\", {}))) or \"NONE\")
"' | sed 's/^/  /'
done
