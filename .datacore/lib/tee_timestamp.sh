#!/usr/bin/env bash
# Write each line with a timestamp prefix to both stdout and the log file.
# Usage: tee_timestamp.sh <log_file> -- <command> [args...]   (runs it, stdout+stderr)
#        <command> 2>&1 | tee_timestamp.sh <log_file>        (reads stdin)
# Each line gets prefixed with ISO timestamp and space.
#
# The first form is the one for a managed cron line: cron_install.py refuses a
# compound command (a pipe), so `job_verify.py ... | tee_timestamp.sh log`
# failed the whole host installation (agent_host_setup.sh, 2026-11-05). It exits
# with the command's own status.

LOG="${1:?usage: $0 <log_file> [-- <command> [args...]]}"
shift
mkdir -p "$(dirname "$LOG")"

stamp() {
  while IFS= read -r line || [ -n "$line" ]; do
    TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf '%s %s\n' "$TS" "$line" | tee -a "$LOG"
  done
}

if [ "${1:-}" = "--" ]; then
  shift
  [ $# -gt 0 ] || { echo "usage: $0 <log_file> -- <command> [args...]" >&2; exit 2; }
  set -o pipefail
  "$@" 2>&1 | stamp
  exit $?
fi
stamp
