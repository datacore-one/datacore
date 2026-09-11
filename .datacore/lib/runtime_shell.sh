#!/bin/bash
# Shared bootstrap for scheduled core jobs. Source from the installed wrapper.
datacore_runtime_init() {
  LIB="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return 2
  export DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
  STATE="${DATACORE_STATE:-$HOME/.datacore/state}"
  export DATACORE_STATE="$STATE"
  umask 0077
  mkdir -p -- "$STATE" || return 2
  local c
  local candidates=()
  if [ -n "${DATACORE_PYTHON:-}" ]; then
    # A configured runtime is a contract, not a hint to use unrelated globals.
    candidates=("$DATACORE_PYTHON")
  else
    candidates=("$LIB/../../venv/bin/python" python3.14 python3.13 python3.12 python3.11 python3.10
                /opt/homebrew/bin/python3 /usr/local/bin/python3 python3)
  fi
  PY=""
  for c in "${candidates[@]}"; do
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -I -c 'import sys, yaml, org_workspace; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$c"
      export DATACORE_PYTHON="$PY"
      return 0
    fi
  done
  echo 'FATAL: configured or installed Python must support Python >=3.10, PyYAML and org-workspace' >&2
  return 127
}
