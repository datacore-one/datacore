#!/usr/bin/env bash
# Which layer breaks Tailscale when another VPN is up?
#
# Run it TWICE — once with the VPN off (baseline), once with it on — and diff.
# On 2026-09-07 a work VPN left on overnight made the mac unable to reach the
# Gitea host, which failed mac-seq-gap five times. seq-gap no longer treats
# that as an error (datacore#150), but the connectivity itself is unfixed
# because nobody has yet measured WHICH layer fails.
#
# The layers, in the order they can break:
#   1 route    Tailscale installs 100.64/10, MORE specific than the 0.0.0.0/1
#              pair a full-tunnel VPN pushes, so routing usually survives.
#   2 control  tailscaled needs the coordination server to keep the map fresh.
#   3 path     a direct peer path needs UDP; blocked, it falls back to DERP.
#   4 derp     if the VPN blocks DERP too, peers become unreachable with the
#              route still perfectly in place — the confusing case.
#
#   ./diagnose_tailscale_vpn.sh [peer-ip] [port]
set -u
PEER="${1:-100.115.67.71}"
PORT="${2:-2222}"
TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
line() { printf '\n── %s\n' "$1"; }

printf 'tailscale/VPN diagnostic — %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

line "1 route"
netstat -rn -f inet 2>/dev/null | awk '$1=="default"{print "  default -> "$2" ("$NF")"}' | head -3
netstat -rn -f inet 2>/dev/null | grep -E '^(100\.64|0\.0\.0\.0/1|128\.0\.0\.0/1)' \
  | awk '{print "  "$1" -> "$2" ("$NF")"}' | head -5
echo "  (a 0.0.0.0/1 pair means the VPN is full-tunnel; 100.64/10 should still win)"

line "2 control"
"$TS" status --peers=false 2>&1 | head -3 | sed 's/^/  /'

line "3+4 path and relay"
"$TS" netcheck 2>&1 | grep -iE "udp|ipv4|nearest|derp latency" | head -6 | sed 's/^/  /'

line "peer $PEER"
"$TS" ping -c 2 --timeout 5s "$PEER" 2>&1 | head -3 | sed 's/^/  /'
if nc -z -w 5 "$PEER" "$PORT" 2>/dev/null; then
  echo "  tcp $PORT: OPEN — git over ssh will work"
else
  echo "  tcp $PORT: UNREACHABLE"
fi

line "verdict"
echo "  route ok + control ok + peer ping fails  -> the VPN blocks UDP and DERP (layer 3/4)"
echo "  route missing 100.64/10                  -> the VPN captured the range (layer 1)"
echo "  control 'Logged out'/stale               -> tailscaled cannot reach the coordinator (layer 2)"
