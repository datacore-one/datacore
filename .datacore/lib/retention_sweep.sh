#!/usr/bin/env bash
# Age out what accumulates forever.
#
# Two archives grew without a retention policy until 2026-09-19, on a host that
# had reached 87% of a 154GB disk: winston state backups (30 dailies, 5.4GB,
# ~190MB/day) and polymarket scans (227 files, 2.7GB since 2026-05-30). Neither
# is a backup of last resort -- the winston tarballs sit on the same machine
# they would be restoring, and the scans are raw input that has already been
# processed. Keeping every one of them forever buys nothing and costs the disk
# that every job on the box shares.
#
# Deletes by age, never by count: a gap in the schedule must not silently
# shorten the window that survives.
set -uo pipefail

sweep() {  # dir, days, glob, label
  local dir=$1 days=$2 glob=$3 label=$4
  [ -d "$dir" ] || { echo "$label: $dir absent, nothing to do"; return 0; }
  local before after
  before=$(find "$dir" -maxdepth 1 -name "$glob" -type f | wc -l)
  find "$dir" -maxdepth 1 -name "$glob" -type f -mtime +"$days" -delete
  after=$(find "$dir" -maxdepth 1 -name "$glob" -type f | wc -l)
  echo "$label: kept $after of $before (>${days}d removed), $(du -sh "$dir" 2>/dev/null | cut -f1) remaining"
}

sweep "$HOME/backups/winston"              14 'plur-state-*.tar.gz' 'winston-backups'
sweep "$HOME/polymarket-scanner/data/scans" 30 '*.jsonl'             'polymarket-scans'
