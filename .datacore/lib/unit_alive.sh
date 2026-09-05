#!/usr/bin/env bash
# Stamp an artifact while a long-running unit is active, so a job contract
# can see a service that has no artifact of its own (the Miles bot).
#   unit_alive.sh <unit> <artifact>     writes an ISO stamp; leaves the file alone when the unit is down
set -u
unit="${1:?unit}"; out="${2:?artifact path}"
mkdir -p "$(dirname "$out")"
if systemctl is-active --quiet "$unit"; then date -u +%FT%TZ > "$out"; else echo "$(date -u +%FT%TZ) $unit not active" >&2; exit 1; fi
