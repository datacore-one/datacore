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

# WHAT is swept is this machine's own list, not ours (INS-3): one line per
# archive in $RETENTION_CONF (default ~/.datacore/retention.conf):
#   <dir> <days> <glob> <label>        e.g.  ~/backups/app 14 'state-*.tar.gz' app-backups
# `~` and $HOME expand; # starts a comment. No file: nothing is swept, said loudly.
CONF="${RETENTION_CONF:-$HOME/.datacore/retention.conf}"
if [ ! -r "$CONF" ]; then
  echo "retention: no $CONF -- nothing declared, nothing swept"
  exit 0
fi
while read -r dir days glob label; do
  case "$dir" in ''|'#'*) continue ;; esac
  dir="${dir/#\~/$HOME}"; dir="${dir//\$HOME/$HOME}"
  glob="${glob#\'}"; glob="${glob%\'}"
  sweep "$dir" "$days" "$glob" "${label:-$dir}"
done < "$CONF"
