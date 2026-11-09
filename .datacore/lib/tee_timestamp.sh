#!/usr/bin/env bash
# Read stdin and write each line with a timestamp prefix to both stdout and the log file.
# Usage: <command> 2>&1 | tee_timestamp.sh <log_file>
# Each line gets prefixed with ISO timestamp and space.

LOG="${1:?usage: $0 <log_file>}"
mkdir -p "$(dirname "$LOG")"
while IFS= read -r line || [ -n "$line" ]; do
  TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s %s\n' "$TS" "$line" | tee -a "$LOG"
done
