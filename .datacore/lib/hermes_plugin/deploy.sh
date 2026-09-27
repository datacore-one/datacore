#!/usr/bin/env bash
# Install the Datacore plugin into a Hermes host and enable it.
#
#   .datacore/lib/hermes_plugin/deploy.sh [host ...]
#
# No hosts given: the roster's `roles.hermes_hosts` machines, by ssh alias
# (.datacore/registry/infrastructure.yaml) -- never a list of ours (INS-3).
#
# The plugin is a user plugin (~/.hermes/plugins/datacore/): Hermes scans that
# directory but leaves user plugins OFF until config.yaml lists them under
# plugins.enabled — untrusted-code gate. This script copies the files and
# reports the config line; it does NOT edit config.yaml, because a gateway
# reading a half-written config is a worse failure than an unenabled plugin.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ $# -gt 0 ]; then
  HOSTS=("$@")
else
  HOSTS=()
  while IFS= read -r h; do [ -n "$h" ] && HOSTS+=("$h"); done \
    < <(python3 "$SRC/../jobs/manifest.py" role-ssh hermes_hosts 2>/dev/null)
fi
[ ${#HOSTS[@]} -gt 0 ] || { echo "no hosts: pass them, or set roles.hermes_hosts in .datacore/registry/infrastructure.yaml" >&2; exit 2; }

for H in "${HOSTS[@]}"; do
  echo "── $H"
  ssh -o ConnectTimeout=20 "$H" 'mkdir -p ~/.hermes/plugins/datacore'
  rsync -q --checksum "$SRC/plugin.yaml" "$SRC/__init__.py" "$H:.hermes/plugins/datacore/"
  ssh -o ConnectTimeout=20 "$H" '
    set -e
    PY=$(ls /usr/local/lib/hermes-agent/venv/bin/python ~/.hermes/hermes-agent/venv/bin/python 2>/dev/null | head -1)
    cd ~/.hermes/plugins
    "$PY" -c "
import sys; sys.path.insert(0, \".\")
import datacore as d
i = d.identity(refresh=True)
print(\"  identity:\", i[\"principal\"] or \"-\", \"as\", i[\"actor\"] or \"-\",
      \"| in force\" if i[\"ok\"] else \"| INERT: \" + i[\"why\"])
print(\"  memory block:\", d.sync_memory_block())
"
    if grep -qE "^\s+- datacore$" ~/.hermes/config.yaml 2>/dev/null; then
      echo "  config: already under plugins.enabled"
    else
      echo "  config: NOT enabled — add to ~/.hermes/config.yaml:"
      echo "            plugins:"
      echo "              enabled:"
      echo "                - datacore"
      echo "          then: systemctl restart hermes-gateway (or --user)"
    fi'
done
