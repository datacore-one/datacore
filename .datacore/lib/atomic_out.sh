#!/usr/bin/env bash
# Write a job's artifact atomically: nothing can read it half-written.
#
#     atomic_out.sh ~/.datacore/state/seq-gap.log -- python3 detectors/seq_gap.py --fetch
#
# WHY. A manifest cmd of the shape `producer > ~/.datacore/state/x.log` truncates
# the artifact the instant the shell starts and fills it over however long the
# producer takes. job_verify reads that file on its own schedule, so for the
# whole of that window the artifact on disk is empty or half a report -- and a
# contract reading it fails, loudly, about a producer that is running normally
# and will finish fine.
#
# Observed twice in two days, both times as a contract failure with no defect
# behind it:
#   2026-09-20  mac-suite-audit, a ten-minute run, read with only its header
#               written: "20 suite(s), each in its own pytest process".
#   2026-09-21  mac-seq-gap, a ~60-second fetch across 67 logs, read as
#               "file is empty".
#
# The first was fixed in place, in that one job's runner. This is the same fix
# for the other twelve, because the next producer to grow slow enough to be
# caught would have been a third instance of a defect already diagnosed twice.
#
# The temp file is a sibling of the destination so the rename is on one
# filesystem and therefore atomic. A reader sees the previous complete artifact
# until the moment it sees the new complete one; it never sees a partial file,
# and a producer that dies mid-run leaves the last good artifact in place rather
# than a truncated one -- which is the honest outcome, since freshness is what
# catches a producer that stopped running (max_age_hours, judged in awake time).
set -uo pipefail

if [ "$#" -lt 3 ] || [ "$2" != "--" ]; then
  echo "usage: atomic_out.sh <artifact-path> -- <command> [args...]" >&2
  exit 2
fi

dest="$1"; shift 2
# Expand a leading ~ the way the shell would have in the original redirect.
case "$dest" in "~/"*) dest="$HOME/${dest#\~/}" ;; esac

mkdir -p "$(dirname -- "$dest")" || exit 2
tmp="$(mktemp "$(dirname -- "$dest")/.$(basename -- "$dest").XXXXXX")" || exit 2

"$@" > "$tmp" 2>&1
rc=$?

mv -f "$tmp" "$dest" || { rm -f "$tmp"; exit 2; }
exit "$rc"
