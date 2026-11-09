#!/usr/bin/env python3
"""PreToolUse hook: nothing is published to a hosted page or shared link without asking (MEM-17).

Work products stay local. A publish -- the Artifact tool's publish (the default
action), `gh gist create`, `surge`, `netlify deploy`, `vercel` -- makes a page
reachable by others, so an interactive session asks the owner first
(permissionDecision "ask"). Reading, listing or opening an existing artifact,
and every other command, pass untouched.

Unattended principals are governed by tool_policy (.datacore/config/
tool_effects.yaml); this hook covers interactive Claude Code sessions.

Registration (Claude Code settings, PreToolUse):
    {"matcher": "Artifact|Bash",
     "hooks": [{"type": "command", "timeout": 5,
                "command": "python3 ~/Data/.datacore/lib/hooks/publish_guard.py"}]}

Stdlib only. Any failure allows: a broken guard must not block tool use.
"""
from __future__ import annotations

import json
import re
import shlex
import sys

ARTIFACT_PUBLISH = {"publish", ""}          # an omitted action means publish
# A command segment that publishes: the program, then what makes it a publish.
PUBLISH_COMMANDS = [
    ("gh gist create", re.compile(r"^gh\s+gist\s+create\b")),
    ("surge", re.compile(r"^(?:npx\s+(?:-y\s+)?)?surge\b")),
    ("netlify deploy", re.compile(r"^(?:npx\s+(?:-y\s+)?)?netlify(?:-cli)?\s+deploy\b")),
    ("vercel deploy", re.compile(r"^(?:npx\s+(?:-y\s+)?)?vercel\b(?!\s+(?:login|logout|whoami|ls|list|env|pull|dev|help|--version|-v)\b)")),
]
_SEGMENT = re.compile(r"&&|\|\||;|\|")


def publishing_command(command: str) -> str:
    """Name of the publishing command in a shell line, or ''."""
    for segment in _SEGMENT.split(command):
        seg = segment.strip()
        # drop leading VAR=value assignments and `env`/`exec` wrappers
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] in ("env", "exec", "command")):
            words = words[1:]
        seg = " ".join(words)
        for name, rx in PUBLISH_COMMANDS:
            if rx.search(seg):
                return name
    return ""


def what_publishes(tool: str, inp: dict) -> str:
    if tool == "Artifact":
        action = str(inp.get("action") or "").strip().lower()
        if action in ARTIFACT_PUBLISH and not inp.get("asset"):
            return "an Artifact publish (a hosted page)"
        if action in ARTIFACT_PUBLISH:
            return "an upload to a hosted Artifact"
        return ""
    if tool == "Bash":
        name = publishing_command(str(inp.get("command") or ""))
        return f"`{name}` (a public or shareable link)" if name else ""
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    what = what_publishes(str(payload.get("tool_name") or ""), payload.get("tool_input") or {})
    if not what:
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": (
            f"Publish guard: this is {what}. Work products stay local unless the owner "
            "says to publish -- confirm this publish was asked for."),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — guard failure must not block tool use
        sys.exit(0)
