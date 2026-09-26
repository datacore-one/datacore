#!/usr/bin/env python3
"""Config Protection Hook (PreToolUse: Bash, Edit, Write, MultiEdit)

Blocks the moves that weaken a check instead of fixing what it caught:

* modifying a linter/formatter config file — agents weaken configs to make
  checks pass instead of fixing code;
* switching a safety guard off (MEM-09, 2026-09-26): an edit of a Claude
  settings file that drops a guard's hook, a rewrite of a guard into a stub
  that can no longer block (`sys.exit(0)`), and the git bypasses
  (`SKIP_PRE_PUSH=1`, `--no-verify`, `core.hooksPath`). Changing a guard is
  still possible; unplugging it is not. What it caught is understood first.

Paths are recognised by their shape, not by $HOME, so the guard judges the
same call the same way whoever runs it.

Exit codes:
  0 = allow
  2 = block
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# The Datacore guards a session wires. Dropping one from a settings file, or
# rewriting one so it cannot block, switches it off.
GUARDS = {
    "restricted_hosts_guard.py", "org_date_prewrite.py", "tool_policy_guard.py",
    "config_protection.py", "space_policy_guard.py", "redaction_guard.py",
    "ledger_write_gate.py", "log_ownership_guard.py", "injection_integrity_guard.py",
}
SETTINGS_RE = re.compile(r"/\.claude/settings(\.local)?\.json$")
GUARD_FILE_RE = re.compile(r"/\.datacore/lib/hooks/([\w.-]+\.py)$")
GITHOOK_RE = re.compile(r"/\.datacore/githooks/[\w.-]+$")
# A Python guard blocks by exiting 2 or answering "deny"; a git hook by a
# non-zero exit. Content with none of these cannot block anything.
PY_BLOCKS = re.compile(r"exit\(\s*2\s*\)|return\s+2\b|[\"']deny[\"']|SystemExit\(\s*2\s*\)")
SH_BLOCKS = re.compile(r"\bexit\s+[1-9]")
BYPASSES = [
    (re.compile(r"\bSKIP_PRE_PUSH="), "SKIP_PRE_PUSH skips the pre-push guard"),
    (re.compile(r"--no-verify\b"), "--no-verify skips the git guards"),
    (re.compile(r"\bcore\.hooksPath\b"), "changing core.hooksPath unplugs the git guards"),
    (re.compile(r"\bgit\s+commit\b[^\n;&|]*\s-n\b"), "git commit -n skips the git guards"),
]

PROTECTED_BASENAMES = {
    # ESLint
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
    ".eslintrc.yml", ".eslintrc.yaml",
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
    "eslint.config.ts", "eslint.config.mts",
    # Prettier
    ".prettierrc", ".prettierrc.js", ".prettierrc.cjs", ".prettierrc.json",
    ".prettierrc.yml", ".prettierrc.yaml",
    "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs",
    # Biome
    "biome.json", "biome.jsonc",
    # Python linters
    ".ruff.toml", "ruff.toml",
    ".flake8", ".pylintrc", "setup.cfg",
    # Shell / Style / Markdown
    ".shellcheckrc", ".stylelintrc", ".stylelintrc.json",
    ".markdownlint.json", ".markdownlint.yaml", ".markdownlintrc",
    # Go
    ".golangci.yml", ".golangci.yaml",
    # Rust
    "clippy.toml", ".clippy.toml",
}

def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _after(tool: str, tool_input: dict, file_path: str) -> str | None:
    """The file's content once this call has run; None when it cannot be told."""
    if tool == "Write":
        return str(tool_input.get("content", ""))
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    text = _read(file_path)
    for e in edits or []:
        if not isinstance(e, dict):
            return None
        old, new = str(e.get("old_string", "")), str(e.get("new_string", ""))
        if e.get("replace_all"):
            text = text.replace(old, new)
        elif old in text:
            text = text.replace(old, new, 1)
        else:
            return None
    return text


def switch_off(tool: str, tool_input: dict) -> str | None:
    """Why this call switches a safety guard off, or None."""
    if not isinstance(tool_input, dict):
        return None
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        for rx, why in BYPASSES:
            if rx.search(command):
                return why
        return None
    if tool not in ("Edit", "Write", "MultiEdit"):
        return None
    file_path = str(tool_input.get("file_path", "") or "")
    if SETTINGS_RE.search(file_path):
        if tool == "Write":
            before, after = _read(file_path), str(tool_input.get("content", ""))
        else:
            edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
            before = "".join(str(e.get("old_string", "")) for e in edits or [] if isinstance(e, dict))
            after = "".join(str(e.get("new_string", "")) for e in edits or [] if isinstance(e, dict))
        dropped = sorted(g for g in GUARDS if g in before and g not in after)
        if dropped:
            return f"this edit unwires {', '.join(dropped)} from {os.path.basename(file_path)}"
        return None
    m = GUARD_FILE_RE.search(file_path)
    if m and m.group(1) in GUARDS:
        after = _after(tool, tool_input, file_path)
        if after is not None and not PY_BLOCKS.search(after):
            return f"this leaves {m.group(1)} unable to block anything (a do-nothing stub)"
        return None
    if GITHOOK_RE.search(file_path):
        after = _after(tool, tool_input, file_path)
        if after is not None and not SH_BLOCKS.search(after):
            return f"this leaves the git hook {os.path.basename(file_path)} unable to refuse anything"
    return None


def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    why = switch_off(str(data.get("tool_name", "")), data.get("tool_input") or {})
    if why:
        sys.stderr.write(
            f"BLOCKED: {why}. A safety guard is never switched off or replaced "
            "by a stub. Understand what it caught first, then fix the cause; if "
            "the guard itself is wrong, the user changes it.\n"
        )
        sys.exit(2)

    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    file_path = tool_input.get("file_path", "") or tool_input.get("file", "")
    if not file_path:
        sys.stdout.write(raw)
        sys.exit(0)

    basename = os.path.basename(file_path)
    if basename in PROTECTED_BASENAMES:
        sys.stderr.write(
            f"BLOCKED: Modifying {basename} is not allowed. "
            "Fix the source code to satisfy linter/formatter rules instead of "
            "weakening the config. If this is a legitimate config change, "
            "the user can approve it manually.\n"
        )
        sys.exit(2)

    sys.stdout.write(raw)
    sys.exit(0)

if __name__ == "__main__":
    main()
