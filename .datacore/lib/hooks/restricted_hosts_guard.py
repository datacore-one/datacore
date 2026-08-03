#!/usr/bin/env python3
"""Block agent access to hosts that are off-limits to AI.

Some infrastructure is covered by agreements that forbid AI systems touching it
at all. That is not a preference an agent should be trusted to remember across
sessions — a fresh context, a plausible-sounding reason, and the rule is gone.
So it is enforced mechanically, before the command runs, rather than written
down and hoped for.

Reads its deny list from `restricted_hosts.json` beside this file, so hosts can
be added without editing code:

    {
      "hosts": ["igea"],
      "networks": ["192.168.253.", "192.168.254."],
      "note": "shown to the agent when a call is blocked"
    }

`hosts` are matched only when they appear as the *target* of a remote-access
command (ssh, scp, rsync, …), because a bare word like a project name shows up
in innocent paths constantly and blocking on that would be unusable.
`networks` are matched anywhere in the command — an IP literal has no innocent
use in a shell command aimed at these ranges.

Exit 2 blocks the call and returns the message to the agent. Exit 0 allows it.
This hook fails **open** on its own errors: a broken guard that silently blocks
every command is its own outage, and the deny list is a backstop rather than the
only control.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CONFIG = Path(__file__).with_name("restricted_hosts.json")

DEFAULTS = {
    "hosts": [],
    "networks": [],
    "note": "This host is off-limits to AI agents. A human must run this manually.",
}

# Commands that reach another machine. `ssh` covers tunnels (-L/-R/-D) because
# the binary is the same; there is no separate tunnel command to enumerate.
REMOTE_TOOLS = (
    "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet",
    "curl", "wget", "ftp", "socat", "mosh", "ping", "traceroute", "nmap",
)


def load() -> dict:
    if not CONFIG.exists():
        return dict(DEFAULTS)
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def offending(command: str, config: dict) -> str | None:
    """The restricted target this command would reach, if any."""
    lowered = command.lower()

    for network in config["networks"]:
        if network in command:
            return network.rstrip(".") + ".x"

    # A host alias only counts when a remote-access tool is actually involved.
    # Otherwise every `grep igea notes.md` would be blocked, and a guard that
    # cries wolf gets switched off.
    if not any(re.search(rf"\b{re.escape(tool)}\b", lowered) for tool in REMOTE_TOOLS):
        return None

    for host in config["hosts"]:
        # Matches `ssh igea`, `user@igea`, `ssh -N -f -L … igea`, `igea:/path`.
        if re.search(rf"(^|[\s@]){re.escape(host.lower())}($|[\s:/])", lowered):
            return host
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return 0

    config = load()
    target = offending(command, config)
    if target is None:
        return 0

    print(
        f"BLOCKED: this command reaches {target}, which is off-limits to AI agents.\n"
        f"{config['note']}\n"
        "Do not attempt a workaround — no tunnel, proxy, alternate hostname, or "
        "asking another agent to run it. Tell the operator what needs doing and "
        "let them run it themselves.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - fail open, see module docstring
        sys.exit(0)
