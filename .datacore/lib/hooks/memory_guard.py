#!/usr/bin/env python3
"""PreToolUse hook: always-loaded memory never takes a name, amount, host or secret (MEM-19).

The auto-memory index (~/.claude/projects/<project>/memory/MEMORY.md) and the
`description:` line of every file it links are loaded into the system prompt of
EVERY session, so a line there is a disclosure surface. On 2026-09-07 an index
line supplied a customer's name and invoice timing into a live demo. The rule
against it was prose at the top of the file, kept by whoever edited it.

This guard checks what a Write/Edit/MultiEdit puts on those surfaces -- index
lines ("- ...") in MEMORY.md, `description:` lines in any memory/*.md -- for:
private/any IPv4 addresses, internal host names, ssh targets, credential-shaped
tokens, currency amounts, and the customer names in the private denylist
(~/.datacore/private/customer-denylist.yaml `forbidden_content`, read at run
time, never printed). A hit is denied with the categories found (never the
text): write a pointer, keep the detail in the linked file's body.

Registration (Claude Code settings, PreToolUse):
    {"matcher": "Edit|Write|MultiEdit",
     "hooks": [{"type": "command", "timeout": 5,
                "command": "python3 ~/Data/.datacore/lib/hooks/memory_guard.py"}]}

Stdlib + optional PyYAML (denylist only). Any failure allows: a broken guard
must not block editing.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import sys
from pathlib import Path

MEMORY_FILE = re.compile(r"/\.claude/projects/[^/]+/memory/[^/]+\.md$")

GENERIC = [
    ("IP address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("internal host", re.compile(r"\b[\w-]+\.(?:local|internal|lan|ts\.net)\b", re.I)),
    ("ssh target", re.compile(r"\bssh\s+(?:-\S+\s+)*[\w.-]+@[\w.-]+")),
    ("credential", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{10,}|xox[bp]-[\w-]{10,}"
                              r"|AKIA[0-9A-Z]{12,}|[A-Fa-f0-9]{40,}|eyJ[\w-]{20,})")),
    ("amount", re.compile(r"(?:[€$£]\s?\d[\d,.]*\s?[kKmM]?\b|\b\d[\d,.]*\s?[kKmM]?\s?(?:EUR|USD|GBP|CHF)\b"
                          r"|\b(?:EUR|USD|GBP|CHF)\s?\d[\d,.]*)")),
]


def _real_home() -> Path:
    """The account's home, not $HOME (a sandbox or probe may repoint it)."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def _denylist() -> list:
    path = _real_home() / ".datacore" / "private" / "customer-denylist.yaml"
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — no denylist: generic checks only
        return []
    out = []
    for pattern in data.get("forbidden_content") or []:
        try:
            out.append(("customer name", re.compile(str(pattern), re.I)))
        except re.error:
            continue
    return out


def surface_lines(file_path: str, text: str) -> list[str]:
    """The lines of `text` that load into every session from this memory file."""
    index = Path(file_path).name == "MEMORY.md"
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if index and stripped.startswith("- "):
            out.append(line)
        elif stripped.startswith("description:"):
            out.append(line)
    return out


def offences(lines: list[str], patterns) -> list[str]:
    return sorted({label for line in lines for label, rx in patterns if rx.search(line)})


def _written(inp: dict) -> str:
    parts = [inp.get("content"), inp.get("new_string")]
    parts += [e.get("new_string") for e in inp.get("edits") or [] if isinstance(e, dict)]
    return "\n".join(str(p) for p in parts if p)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    inp = payload.get("tool_input") or {}
    file_path = str(inp.get("file_path") or "")
    if not MEMORY_FILE.search(file_path):
        return 0
    hits = offences(surface_lines(file_path, _written(inp)), GENERIC + _denylist())
    if not hits:
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"MEMORY disclosure guard: this puts {', '.join(hits)} on a line that loads into "
            "every session (a MEMORY.md index line or a memory file's description:). Write a "
            "pointer that says what kind of thing it is, with no specifics, and keep the detail "
            "in the linked file's body; credentials belong in the broker."),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — guard failure must not block tool use
        sys.exit(0)
