#!/usr/bin/env python3
"""Cursor hook bridge: run Datacore's guards on Cursor's tool calls.

Datacore's guards are Claude Code hooks. They read Claude's payload shape
(`tool_name` "Bash" / "Write" / "Edit", `tool_input.command`, `file_path`,
`content`, `new_string`) and block by exiting 2 with the reason on stderr.
Cursor sends its own shape: `tool_name` "Shell" / "Write", or, for
`beforeShellExecution`, a bare `command`. Cursor also imports Claude hooks
directly, but the host guard only acts on `tool_name == "Bash"`, so under
Cursor's "Shell" it would allow everything without a word. This bridge
translates each Cursor event into the Claude shape, runs the real guard
scripts unchanged, and turns a block into Cursor's
`{"permission": "deny", ...}`.

It never emits an allow: saying nothing leaves Cursor's own approval flow in
charge. Failures fail open, except for shell commands, where the host guard
is contractual: a shell event this bridge cannot evaluate is denied, as the
guard itself does.

Registered by install.py in <root>/.cursor/hooks.json for `preToolUse` and
`beforeShellExecution`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[2]   # .datacore/lib
GUARDS = {
    "write": [LIB / "hooks" / "org_date_prewrite.py"],
    "shell": [LIB / "hooks" / "restricted_hosts_guard.py"],
}
SHELL_TOOLS = {"Shell", "Bash", "run_terminal_cmd", "terminal"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "StrReplace", "edit_file", "write", "search_replace"}
PATH_KEYS = ("file_path", "path", "target_file", "file", "filePath")
TEXT_KEYS = ("content", "contents", "new_string", "code_edit", "text", "streamingContent")


def normalize(payload: dict) -> tuple[str, dict] | None:
    """(kind, Claude-shaped payload) for an event a guard cares about, else None."""
    event = payload.get("hook_event_name")
    cwd = payload.get("cwd") or next(iter(payload.get("workspace_roots") or []), None)
    if event == "beforeShellExecution" or ("command" in payload and "tool_name" not in payload):
        command = payload.get("command")
        if not isinstance(command, str):
            raise ValueError("shell event without a command string")
        return "shell", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = tool_input.get("working_directory") or cwd
    if tool in SHELL_TOOLS:
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("shell tool call without a command string")
        return "shell", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    if tool in WRITE_TOOLS:
        path = next((tool_input[k] for k in PATH_KEYS if isinstance(tool_input.get(k), str)), "")
        claude_input: dict = {"file_path": path}
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            claude_input["edits"] = [e for e in edits if isinstance(e, dict)]
        else:
            claude_input["content"] = next(
                (tool_input[k] for k in TEXT_KEYS if isinstance(tool_input.get(k), str)), "")
        return "write", {"tool_name": "Write", "tool_input": claude_input, "cwd": cwd}
    return None


def run_guards(kind: str, claude_payload: dict) -> str | None:
    """The first guard's block reason, or None if every guard allows."""
    for guard in GUARDS[kind]:
        result = subprocess.run([sys.executable, str(guard)], input=json.dumps(claude_payload),
                                capture_output=True, text=True, timeout=20)
        if result.returncode == 2:
            return (result.stderr or result.stdout or f"blocked by {guard.name}").strip()
    return None


def deny(reason: str) -> None:
    print(json.dumps({
        "permission": "deny",
        "user_message": "Datacore guard blocked this action.",
        "agent_message": reason,
    }))


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        found = normalize(payload)
    except ValueError as exc:
        deny(f"Could not evaluate this shell command ({exc}); Datacore's host guard refuses what it cannot check.")
        return 0
    if found is None:
        return 0
    kind, claude_payload = found
    try:
        reason = run_guards(kind, claude_payload)
    except Exception as exc:  # noqa: BLE001 — see module docstring on failure behaviour
        if kind == "shell":
            deny(f"Datacore's host guard could not run ({exc}); refusing rather than allowing unchecked.")
        return 0
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — a crash must not block unrelated work
        sys.exit(0)
