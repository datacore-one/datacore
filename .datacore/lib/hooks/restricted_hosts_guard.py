#!/usr/bin/env python3
"""Block agent access to hosts that are off-limits to AI.

Some infrastructure is covered by agreements that forbid AI systems touching it
at all. That is not a preference an agent should be trusted to remember across
sessions — a fresh context, a plausible-sounding reason, and the rule is gone.
So it is enforced mechanically, before the command runs, rather than written
down and hoped for.

Configuration is split, because the host names themselves are confidential:

* ``restricted_hosts.json`` beside this file — **tracked, public**. Generic
  entries only: private network ranges, the default message. Never a customer
  host name; this repository is public.
* ``~/.datacore/private/customer-denylist.yaml`` under ``restricted_hosts:`` —
  **never in any repository**. The actual host names live here and are unioned
  with the public config at load time.

Naming a customer in the public file is itself the disclosure the agreement
exists to prevent, and it happened once already.

What counts as reaching a host
------------------------------
Matching the raw command text is both too weak and too strong. Too weak because
``git push origin main`` names no host at all — the target is in ``.git/config``.
Too strong because ``grep acme notes.md`` mentions a name while touching
nothing, and a guard that cries wolf gets switched off.

So targets are *extracted* rather than pattern-matched:

* URLs anywhere in the command (``https://git.example.com/x``)
* ``scp``-style targets (``user@host:/path``)
* the destination argument of a remote tool (``ssh myalias``)
* for a git subcommand that talks to a network, the resolved URLs of the
  remotes in the repository it would operate on

Host names match on **domain-label** boundaries, so an entry ``acme`` catches
``git.acme.si`` and ``acme`` but not ``acmecorp``.

Failure behaviour
-----------------
Fails **open** for commands that cannot reach anywhere — a broken guard that
blocks every command is its own outage. Fails **closed** when it cannot finish
evaluating a command that demonstrably *can* reach a network. An error while
resolving a git remote must not be the reason a prohibited push succeeds.

Exit 2 blocks the call and returns the message to the agent. Exit 0 allows it.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

CONFIG = Path(__file__).with_name("restricted_hosts.json")
PRIVATE_CONFIG = Path.home() / ".datacore" / "private" / "customer-denylist.yaml"

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

# git subcommands that open a connection. `git push` was previously invisible
# to this guard, which is the hole that let a prohibited push through.
GIT_NETWORK_SUBCOMMANDS = frozenset({
    "push", "pull", "fetch", "clone", "ls-remote", "remote", "submodule",
    "request-pull", "send-email", "svn", "archive",
})

URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://([^/\s'\"]+)", re.IGNORECASE)
SCP_RE = re.compile(r"(?:^|[\s'\"])(?:[\w.-]+@)([\w.-]+):", re.IGNORECASE)


class Unevaluable(Exception):
    """Evaluation failed for a command that can reach a network — fail closed."""


def load() -> dict:
    """Public config unioned with the private host list."""
    config = dict(DEFAULTS)
    try:
        if CONFIG.exists():
            config.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        pass

    if not PRIVATE_CONFIG.exists():
        return config
    try:
        import yaml
        loaded = yaml.safe_load(PRIVATE_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — unreadable overlay is fail-closed
        raise Unevaluable(f"private host list unreadable: {exc}") from exc

    private = loaded.get("restricted_hosts") or {}
    config["hosts"] = list(config["hosts"]) + list(private.get("hosts") or [])
    config["networks"] = list(config["networks"]) + list(private.get("networks") or [])
    if private.get("note"):
        config["note"] = private["note"]
    return config


def _matches(target: str, host: str) -> bool:
    """True when `host` appears in `target` as a whole domain label."""
    return re.search(
        rf"(?<![A-Za-z0-9-]){re.escape(host)}(?![A-Za-z0-9-])", target, re.IGNORECASE
    ) is not None


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _effective_dir(tokens: list[str], cwd: str | None) -> Path:
    """Where a git command would run: `-C <path>` if given, else the cwd."""
    for index, token in enumerate(tokens):
        if token == "-C" and index + 1 < len(tokens):
            return Path(tokens[index + 1]).expanduser()
    return Path(cwd) if cwd else Path.cwd()


def _git_remote_urls(directory: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "remote", "-v"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unevaluable(f"could not resolve git remotes: {exc}") from exc
    if result.returncode != 0:
        return []  # not a repository — nothing to reach
    return [line.split()[1] for line in result.stdout.splitlines() if len(line.split()) > 1]


SEGMENT_RE = re.compile(r"&&|\|\||[;\n|]")


def _segments(command: str) -> list[list[str]]:
    """The command split on shell operators, tokenised.

    A bare destination (`ssh myalias`) can only be read from the segment that
    actually invokes the remote tool. Scanning the whole command instead means
    the word "scp-style" anywhere — a commit message, a comment — switches on
    host-matching against every unrelated token in every other segment. That
    produced a false positive on a `grep` in an `&&`-chained command.
    """
    return [_tokens(part) for part in SEGMENT_RE.split(command) if part.strip()]


def _explicit_targets(command: str, tokens: list[str]) -> list[str]:
    """Hosts named directly in the command."""
    # URLs and user@host: forms are unambiguous wherever they appear.
    targets = [match.group(1) for match in URL_RE.finditer(command)]
    targets += [match.group(1) for match in SCP_RE.finditer(command)]

    for segment in _segments(command):
        # Skip leading env assignments: `FOO=bar ssh host`
        head = next((t for t in segment if "=" not in t.split("/")[-1] or t.startswith("-")), None)
        if head is None or Path(head).name not in REMOTE_TOOLS:
            continue
        for token in segment[1:]:
            if token.startswith("-"):
                continue
            candidate = token.split("@")[-1]
            if ":" in candidate:
                # `host:/path` — the colon comes before any slash, so this is a
                # destination rather than a local path.
                candidate = candidate.split(":", 1)[0]
            elif "/" in candidate:
                continue  # a local path, not a host
            if candidate and Path(candidate).name not in REMOTE_TOOLS:
                targets.append(candidate)
    return targets


def _git_targets(tokens: list[str], cwd: str | None) -> list[str]:
    """Remote URLs a git subcommand would contact."""
    if not tokens or Path(tokens[0]).name != "git":
        return []
    skip = set()
    for index, token in enumerate(tokens):
        if token == "-C" and index + 1 < len(tokens):
            skip.add(index + 1)
    subcommands = [
        token for index, token in enumerate(tokens[1:], start=1)
        if not token.startswith("-") and index not in skip
    ]
    if not subcommands or subcommands[0] not in GIT_NETWORK_SUBCOMMANDS:
        return []
    return _git_remote_urls(_effective_dir(tokens, cwd))


def offending(command: str, config: dict, cwd: str | None = None) -> str | None:
    """The restricted target this command would reach, if any."""
    for network in config["networks"]:
        if network in command:
            return network.rstrip(".") + ".x"

    tokens = _tokens(command)
    targets = _explicit_targets(command, tokens) + _git_targets(tokens, cwd)

    for target in targets:
        host_part = urlsplit(target).hostname or target
        for host in config["hosts"]:
            if _matches(host_part, host) or _matches(target, host):
                return host
    return None


def _can_reach_network(command: str, tokens: list[str]) -> bool:
    """Whether this command is capable of contacting a host at all."""
    lowered = command.lower()
    if any(re.search(rf"\b{re.escape(tool)}\b", lowered) for tool in REMOTE_TOOLS):
        return True
    if tokens and Path(tokens[0]).name == "git":
        return any(token in GIT_NETWORK_SUBCOMMANDS for token in tokens[1:])
    return bool(URL_RE.search(command))


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
    cwd = payload.get("cwd")

    try:
        config = load()
        target = offending(command, config, cwd)
    except Unevaluable as exc:
        if not _can_reach_network(command, _tokens(command)):
            return 0  # cannot reach anywhere — no reason to block
        print(
            f"BLOCKED: could not verify this command's target ({exc}).\n"
            "A command that can reach a network is refused when the restricted-host "
            "list cannot be read, rather than allowed by default.",
            file=sys.stderr,
        )
        return 2

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
    except Exception:  # noqa: BLE001 - see module docstring on failure behaviour
        sys.exit(0)
