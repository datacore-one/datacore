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
* going past the ledger (owner decision 2026-09-28): setting the removed
  override flag, or deleting, moving or rewriting a sequence witness or stop
  record under `.datacore/state/seq-hwm/`. The owner repairs a stale log from
  a terminal of their own (.datacore/docs/recovery.md); no session does.

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

# Going past the ledger. On 2026-09-27 an unattended job met the (correct)
# StaleLogError, overwrote and deleted the witness and appended with an
# override flag, forking a log. Reading a mark is diagnosis and stays open.
LEDGER_OVERRIDE = re.compile(
    r"DATACORE_HWM_OVERRIDE\s*=|\b(environ|putenv|setenv)\b.{0,16}DATACORE_HWM_OVERRIDE")
_MUTATES = (r"((?<![\w.-])(rm|mv|cp|unlink|truncate|shred|tee|ln|install|rsync|rename)\b|\bsed\s+-i"
            r"|\bperl\s+-\w*i|(?<![0-9&>-])>{1,2}(?!&|\s*/dev/null)|\s-delete\b"
            r"|\.(unlink|rename|replace|write_text|write_bytes|touch)\(|\bos\.(remove|unlink|rename|replace)\b"
            r"|\bshutil\.)")
WITNESS_TOUCH = re.compile(_MUTATES + r".*seq-hwm|seq-hwm.*" + _MUTATES, re.S)
WITNESS_FILE = re.compile(r"/\.datacore/state/seq-hwm(/|$)")
LEDGER_WHY = ("this goes past the ledger ({what}; owner decision 2026-09-28). Stop the job, "
              "record it and alert The Firm; the owner repairs a stale log by "
              ".datacore/docs/recovery.md")


def ledger_bypass(tool: str, tool_input: dict) -> str | None:
    """Why this call goes past the ledger's stale-log refusal, or None."""
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if LEDGER_OVERRIDE.search(command):
            return LEDGER_WHY.format(what="it sets the removed ledger override flag")
        if WITNESS_TOUCH.search(command):
            return LEDGER_WHY.format(what="it deletes, moves or rewrites a sequence witness")
        return None
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or "")
        if WITNESS_FILE.search(path):
            return LEDGER_WHY.format(what="it rewrites a sequence witness")
    return None


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


# Reading the hooks-path setting is diagnosis (owner-approved 2026-09-30: six read-only
# lookups refused in one session). A mention counts as a read only when it is a
# `git config` lookup that names the key with nothing after it -- no value, no
# --unset/--add/--replace-all -- up to the end of that command. Every other mention
# (a value, `-c key=`, GIT_CONFIG_PARAMETERS, an edit) is still a switch-off.
_HOOKS_PATH_READ = re.compile(
    r"\bgit(?:\s+-C\s+\S+)?\s+config"
    r"(?:\s+--(?:global|local|system|worktree|show-origin|show-scope|get|get-all|includes|null|file\s+\S+))*"
    r"\s+core\.hooksPath\s*(?=$|[;&|)\n])")


def _only_reads_hooks_path(command: str) -> bool:
    mentions = len(BYPASSES[2][0].findall(command))
    return mentions > 0 and mentions == len(_HOOKS_PATH_READ.findall(command))


def switch_off(tool: str, tool_input: dict) -> str | None:
    """Why this call switches a safety guard off, or None."""
    if not isinstance(tool_input, dict):
        return None
    why = ledger_bypass(tool, tool_input)
    if why:
        return why
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        for rx, why in BYPASSES:
            if rx.search(command):
                if rx is BYPASSES[2][0] and _only_reads_hooks_path(command):
                    continue
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
