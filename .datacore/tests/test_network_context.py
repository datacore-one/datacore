"""Name the cause of an unreachable fleet, or say plainly that you cannot.

mac-seq-gap alerted for three days with "regex did not match". True and
useless: a work VPN had captured the route to the Gitea host, every fetch
hung 75 s, and the job was killed before writing anything. The artifact must
carry the reason, in words that name the next action."""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import network_context as nc  # noqa: E402

# Real shapes. macOS prints the split-default pair as 0/1 and 128.0/1.
MAC_VPN = """Destination        Gateway            Flags        Netif Expire
default            192.168.1.254      UGScg           en0
0/1                10.8.0.1           UGSc          utun6
128.0/1            10.8.0.1           UGSc          utun6
100.64/10          link#18            UCS           utun4
192.168.1          link#4             UCS             en0
"""

MAC_CLEAN = """Destination        Gateway            Flags        Netif Expire
default            192.168.1.254      UGScg           en0
100.64/10          link#18            UCS           utun4
192.168.1          link#4             UCS             en0
"""

LINUX_VPN = """default via 10.0.0.1 dev eth0
0.0.0.0/1 via 10.8.0.1 dev tun0
128.0.0.0/1 via 10.8.0.1 dev tun0
100.64.0.0/10 dev tailscale0
"""


def test_a_full_tunnel_vpn_is_detected_by_its_split_default_pair():
    ok, detail = nc.full_tunnel(MAC_VPN)
    assert ok and "utun6" in detail
    ok, detail = nc.full_tunnel(LINUX_VPN)
    assert ok and "tun0" in detail


def test_no_vpn_is_not_reported_as_one():
    assert nc.full_tunnel(MAC_CLEAN) == (False, "")


def test_tailscale_is_never_counted_as_the_vpn():
    """Tailscale is a mesh VPN we run deliberately and it coexists with a
    corporate one; calling it the culprit would send the operator to turn off
    the very thing carrying the fleet."""
    assert nc.tailscale_iface(MAC_VPN) == "utun4"
    assert "utun4" not in nc.tunnels(MAC_VPN)
    assert "utun6" in nc.tunnels(MAC_VPN)
    assert nc.tunnels(MAC_CLEAN) == []


def test_default_interface_on_both_platforms():
    assert nc.default_iface(MAC_CLEAN) == "en0"
    assert nc.default_iface(LINUX_VPN) == "eth0"


def test_the_explanation_names_the_vpn_and_the_next_action():
    msg = nc.explain({
        "host": "mac", "default_interface": "en0",
        "full_tunnel_vpn": True, "full_tunnel_detail": "split-default pair via utun6",
        "vpn_interfaces": ["utun6"], "tailscale_interface": "utun4",
        "remotes": {"100.115.67.71": 2222}, "unreachable": {"100.115.67.71": 2222},
    })
    assert "100.115.67.71" in msg
    assert "full-tunnel VPN" in msg and "utun6" in msg
    assert "Not a fault" in msg, "a VPN is a condition of the machine, not a defect"
    assert "local-network access" in msg, "must name what to change"


def test_a_missing_tailscale_route_is_distinguished_from_a_vpn():
    msg = nc.explain({
        "host": "mac", "default_interface": "en0", "full_tunnel_vpn": False,
        "full_tunnel_detail": "", "vpn_interfaces": [], "tailscale_interface": "",
        "remotes": {"100.115.67.71": 2222}, "unreachable": {"100.115.67.71": 2222},
    })
    assert "Tailscale is down" in msg


def test_an_unexplained_outage_says_so_rather_than_inventing_a_cause():
    msg = nc.explain({
        "host": "box", "default_interface": "eth0", "full_tunnel_vpn": False,
        "full_tunnel_detail": "", "vpn_interfaces": [], "tailscale_interface": "utun4",
        "remotes": {"github.com": 22}, "unreachable": {"github.com": 22},
    })
    assert "no VPN" in msg and "remote itself is likely down" in msg


def test_all_reachable_says_so():
    assert "every fleet remote reachable" in nc.explain({
        "host": "mac", "default_interface": "en0", "full_tunnel_vpn": False,
        "full_tunnel_detail": "", "vpn_interfaces": [], "tailscale_interface": "utun4",
        "remotes": {"github.com": 22}, "unreachable": {},
    })
