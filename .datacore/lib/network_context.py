#!/usr/bin/env python3
"""Why the fleet cannot reach itself — named, not guessed.

WHY THIS EXISTS. `mac-seq-gap` alerted for three days with "regex did not
match". True, and useless: the cause was a work VPN capturing the route to
the Gitea host, so every fetch hung 75 s and the job was killed before it
could write anything. Three days of a check reporting the symptom of a
symptom.

A fleet that cannot verify itself must say WHY in the artifact, in words that
name the next action. "20 unverifiable" is a fact; "20 unverifiable — a
full-tunnel VPN is capturing 192.168.1.0/24, which is where blackpi lives"
is a fact someone can act on.

Reports, never decides. Nothing here changes routing, and a VPN is never
treated as a fault: it is a condition of the machine, and the point is to
attribute the degradation honestly rather than blame the check.

    network_context.py            # one human line
    network_context.py --json     # for a detector to embed in its artifact
"""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path

TAILSCALE_CGNAT = "100.64.0.0/10"


def _run(argv: list[str], timeout: int = 8) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def routes() -> str:
    if platform.system() == "Darwin":
        return _run(["netstat", "-rn", "-f", "inet"])
    return _run(["ip", "-4", "route", "show"])


def default_iface(table: str | None = None) -> str:
    table = routes() if table is None else table
    for line in table.splitlines():
        f = line.split()
        if platform.system() == "Darwin":
            if f and f[0] == "default" and len(f) >= 4:
                return f[-1]
        elif f[:1] == ["default"] and "dev" in f:
            return f[f.index("dev") + 1]
    return ""


def full_tunnel(table: str | None = None) -> tuple[bool, str]:
    """Is a VPN capturing everything?

    The tell is the `0.0.0.0/1` + `128.0.0.0/1` pair: two halves of the
    address space, each more specific than `default`, which is how a VPN
    takes the whole table without touching the default route. On macOS the
    same pair prints as `0/1` and `128.0/1`."""
    table = routes() if table is None else table
    halves = [l for l in table.splitlines()
              if re.match(r"^\s*(0\.0\.0\.0/1|0/1|128\.0\.0\.0/1|128\.0/1)\s", l)]
    if len(halves) >= 2:
        ifaces = {l.split()[-1] for l in halves}
        return True, f"split-default pair via {', '.join(sorted(ifaces))}"
    return False, ""


def tunnels(table: str | None = None) -> list[str]:
    """Tunnel interfaces carrying routes, Tailscale's excluded — it is a mesh
    VPN we run on purpose, and it coexists with a corporate one."""
    table = routes() if table is None else table
    ts = tailscale_iface(table)
    found = set()
    for line in table.splitlines():
        for tok in line.split():
            if re.fullmatch(r"(utun|tun|tap|ppp|ipsec)\d+", tok) and tok != ts:
                found.add(tok)
    return sorted(found)


def tailscale_iface(table: str | None = None) -> str:
    table = routes() if table is None else table
    for line in table.splitlines():
        if "100.64" in line:
            f = line.split()
            if f:
                return f[-1]
    return ""


def reachable(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def fleet_remotes(root: Path) -> dict[str, int]:
    """host -> port for every distinct git remote the spaces point at."""
    out: dict[str, int] = {}
    for space in sorted(root.glob("[0-9]-*")):
        if not (space / ".git").exists():
            continue
        url = _run(["git", "-C", str(space), "remote", "get-url", "origin"]).strip()
        m = re.match(r"(?:ssh://)?(?:[^@/]+@)?([^/:]+)(?::(\d+))?", url)
        if not m:
            continue
        host, port = m.group(1), int(m.group(2) or 22)
        if host and not host.startswith("/"):
            out[host] = port
    return out


def context(root: Path | None = None) -> dict:
    root = root or Path(os.environ.get("DATACORE_ROOT") or (Path.home() / "Data"))
    table = routes()
    ft, ft_detail = full_tunnel(table)
    tun = tunnels(table)
    remotes = fleet_remotes(root)
    unreachable = {h: p for h, p in remotes.items() if not reachable(h, p)}
    return {
        "host": socket.gethostname().split(".")[0].lower(),
        "default_interface": default_iface(table),
        "full_tunnel_vpn": ft,
        "full_tunnel_detail": ft_detail,
        "vpn_interfaces": tun,
        "tailscale_interface": tailscale_iface(table),
        "remotes": remotes,
        "unreachable": unreachable,
    }


def explain(ctx: dict | None = None) -> str:
    """One line naming the degradation and the next action, or that all is well."""
    c = ctx or context()
    if not c["unreachable"]:
        return "network: every fleet remote reachable"
    names = ", ".join(sorted(c["unreachable"]))
    if c["full_tunnel_vpn"]:
        return (f"network: {names} unreachable — a full-tunnel VPN is active "
                f"({c['full_tunnel_detail']}), which captures the local subnet "
                f"and with it the direct path to a peer on this LAN. "
                f"Not a fault: disconnect the VPN, or allow local-network access.")
    if c["vpn_interfaces"]:
        return (f"network: {names} unreachable — VPN interface(s) "
                f"{', '.join(c['vpn_interfaces'])} are up. Check whether they "
                f"capture the route to those hosts.")
    if not c["tailscale_interface"] and any(h.startswith("100.") for h in c["unreachable"]):
        return (f"network: {names} unreachable — no Tailscale route is present "
                f"(100.64/10 absent). Tailscale is down on this machine.")
    return (f"network: {names} unreachable, and no VPN or missing Tailscale route "
            f"explains it — the remote itself is likely down.")


def main() -> int:
    c = context()
    if "--json" in sys.argv:
        print(json.dumps(c, indent=2, sort_keys=True))
    else:
        print(explain(c))
    return 1 if c["unreachable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
