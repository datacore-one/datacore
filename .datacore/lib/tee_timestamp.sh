#!/usr/bin/env bash
# Prefix each line with an ISO timestamp and append it to a log file.
#
#   tee_timestamp.sh <log_file> <command> [args...]
#       Runs the command, appends its stdout and stderr to the log, timestamped,
#       and exits with the command's status. This is the form for crontab lines:
#       cron_install refuses a managed line holding a pipe (it cannot tell which
#       job a compound command is), so `job 2>&1 | tee_timestamp.sh log` made
#       agent_host_setup.sh fail its own cron step on hermes and plur-claw
#       (test_cron_install, 2026-11-09).
#   <command> 2>&1 | tee_timestamp.sh <log_file>
#       Reads stdin; writes each line to stdout and the log.

LOG="${1:?usage: $0 <log_file> [command args...]}"
shift
mkdir -p "$(dirname "$LOG")"

stamp() {
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$line"
  done
}

if [ "$#" -gt 0 ]; then
  "$@" 2>&1 | stamp >> "$LOG"
  exit "${PIPESTATUS[0]}"
fi
stamp | tee -a "$LOG"
