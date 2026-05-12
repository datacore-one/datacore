#!/usr/bin/env python3
"""
command_recall_inject.py

PreToolUse hook for Skill / SlashCommand / Agent invocations.

Reads tool args from stdin, extracts the command/skill/agent name, calls
PLUR's hybrid recall via the local MCP store, and emits an additionalContext
block that the harness injects into the agent's context before the tool runs.

Implements DIP-0029 Phase 1 (the fallback mechanism for harnesses that don't
parse the `recall:` frontmatter declared on commands).

Fail-open: any error returns empty context and exit 0. Never block tool execution.

Registration in ~/.claude/settings.json (auto-installed via
configure-hooks.py; substitute $DATACORE_PATH with your Datacore root):

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Skill|SlashCommand|Agent",
            "hooks": [
              {
                "type": "command",
                "command": "python3 $DATACORE_PATH/.datacore/lib/hooks/command_recall_inject.py",
                "timeout": 3
              }
            ]
          }
        ]
      }
    }
"""
from __future__ import annotations

import json
import sys
import os
import subprocess
from pathlib import Path

# Hard timeout for PLUR query (seconds) — must be well under harness 3s budget
PLUR_QUERY_TIMEOUT = 2.0

# Max engrams to inject; token budget cap is approximate
MAX_RESULTS = 6
TOKEN_BUDGET = 2000

# Where the local PLUR store lives — used directly when MCP isn't reachable
PLUR_STORE = Path.home() / ".plur" / "engrams.yaml"


def _emit(context: str = "") -> None:
    """Emit the hook response and exit 0 (fail-open)."""
    if context:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
        print(json.dumps(out))
    sys.exit(0)


def _extract_name(payload: dict) -> str | None:
    """Pull the skill/command/agent name from the hook input payload."""
    tool_name = payload.get("tool_name") or payload.get("toolName")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}

    if tool_name == "Skill":
        return tool_input.get("skill")
    if tool_name == "SlashCommand":
        cmd = tool_input.get("command", "")
        return cmd.lstrip("/").split()[0] if cmd else None
    if tool_name == "Agent":
        return tool_input.get("subagent_type") or tool_input.get("description")
    return None


def _recall_via_mcp_cli(name: str) -> list[dict]:
    """Call PLUR via the local MCP server CLI if available. Returns empty on any error."""
    plur_cli = Path.home() / ".plur" / "bin" / "plur"
    if not plur_cli.exists():
        # Fall back to npx invocation (works if @plur-ai/mcp is installed globally)
        cmd = ["npx", "-y", "@plur-ai/cli", "recall", "--query", name, "--k", str(MAX_RESULTS), "--format", "json"]
    else:
        cmd = [str(plur_cli), "recall", "--query", name, "--k", str(MAX_RESULTS), "--format", "json"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PLUR_QUERY_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("results", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError):
        return []


def _recall_via_yaml_grep(name: str) -> list[dict]:
    """Fallback: BM25-style grep through the YAML store for tag/scope matches.

    Engrams.yaml structure (per @plur-ai/core):
        engrams:
          - id: ENG-YYYY-MMDD-NNN
            scope: command:X | global | project:Y
            statement: >-
              Multi-line text...
            tags:
              - tag1
              - tag2
    """
    if not PLUR_STORE.exists():
        return []
    try:
        text = PLUR_STORE.read_text(encoding="utf-8")
    except OSError:
        return []

    matches: list[dict] = []
    name_lower = name.lower()

    # Split on engram boundary: lines starting with `  - id:` (2-space indent + dash)
    # Use a marker that's unlikely to appear in content.
    MARKER = "\n  - id:"
    blocks = text.split(MARKER)
    # First block is the `engrams:` header — skip it
    for block in blocks[1:]:
        block_lower = block.lower()
        score = 0
        if f"scope: command:{name_lower}" in block_lower:
            score += 5
        if f"- {name_lower}\n" in block_lower and "tags:" in block_lower:
            # crude tag match
            score += 2
        if f"domain: {name_lower}" in block_lower or f"domain: command.{name_lower}" in block_lower:
            score += 2
        if name_lower in block_lower:
            score += 1
        if score == 0:
            continue

        # Extract id (first line of the block)
        first_line = block.splitlines()[0].strip()
        eid = first_line.rstrip(":").strip()

        # Extract statement (handles `>-` folded scalar)
        stmt_lines: list[str] = []
        in_stmt = False
        for line in block.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("statement:"):
                rest = stripped[len("statement:"):].strip()
                if rest in (">", ">-", "|", "|-"):
                    in_stmt = True
                    continue
                if rest:
                    stmt_lines.append(rest)
                    break
            elif in_stmt:
                # End of statement: line indent drops below the statement's body indent
                # In the YAML, the statement body is indented 6 spaces (deeper than the
                # key's 4). Stop when we see a known sibling field starting at 4 spaces.
                key_match = stripped.split(":", 1)[0] if ":" in stripped else ""
                if key_match in (
                    "derivation_count", "pack", "abstract", "derived_from",
                    "domain", "tags", "activation", "type", "visibility", "scope",
                ):
                    break
                if stripped:
                    stmt_lines.append(stripped)

        stmt = " ".join(stmt_lines).strip()
        if stmt:
            matches.append({"id": eid, "statement": stmt, "score": score})

    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:MAX_RESULTS]


def _format_block(name: str, engrams: list[dict]) -> str:
    """Render the injected memory block. Caps tokens approximately."""
    if not engrams:
        return ""

    lines = [
        f"## Relevant memory for `/{name}` (from PLUR via command_recall_inject hook)",
        "",
        "_Per DIP-0029, these engrams were retrieved to steer behavior for this command._",
        "",
    ]
    chars = sum(len(l) for l in lines)
    char_budget = TOKEN_BUDGET * 4  # rough 4 chars/token

    for e in engrams:
        eid = e.get("id", "UNKNOWN")
        stmt = e.get("statement", "").strip()
        if not stmt:
            continue
        entry = f"- **{eid}** — {stmt}"
        if chars + len(entry) + 1 > char_budget:
            break
        lines.append(entry)
        chars += len(entry) + 1

    return "\n".join(lines)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw:
            _emit()
        payload = json.loads(raw)
    except Exception:
        _emit()

    name = _extract_name(payload)
    if not name:
        _emit()

    # Try MCP first (covers BM25 + embeddings hybrid), fall back to YAML grep
    engrams = _recall_via_mcp_cli(name) or _recall_via_yaml_grep(name)
    block = _format_block(name, engrams)
    _emit(block)


if __name__ == "__main__":
    main()
