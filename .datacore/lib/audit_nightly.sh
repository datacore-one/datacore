#!/usr/bin/env bash
# Nightly cross-model audit, one agent's slice (AUD-1; spec .datacore/specs/cross-model-audit.md).
# Run by cadence_run for the templates audit-nightly-<agent>; the agent is the
# host's principal, which cadence_run exports as DATACORE_POLICY_PRINCIPAL.
# Writes 2-datacore/1-tracks/dev/audits/nightly/<date>/<agent>.yaml and nothing else;
# cadence_run judges and commits that one file.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# Cron and hermes shells have a minimal PATH: pick an interpreter that has PyYAML.
for PY in "${AUDIT_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.pyenv/shims/python3" /usr/bin/python3 python3; do
  [ -n "$PY" ] && command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import yaml' >/dev/null 2>&1 && break
  PY=""
done
[ -n "$PY" ] || { echo "audit_nightly: no python3 with PyYAML" >&2; exit 2; }
exec "$PY" "$HERE/cross_model_audit.py" "${AUDIT_MODE:-nightly}" --agent "${DATACORE_POLICY_PRINCIPAL:-}"
