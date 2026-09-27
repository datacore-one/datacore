#!/usr/bin/env python3
"""PreToolUse guard for task files (DIP-0024, DIP-0009; promise MEM-38).

Tasks change only through the task tools (org_workspace_adapter.py, the GTD
MCP tools). A raw text edit bypasses ID generation, the stale-overwrite check
and the transaction lock, which is how dismissed tasks came back and captures
were lost. A warning the agent can read past kept nothing (the guard was
advisory until 2026-09-27), so this REFUSES:

  * Edit / Write of a task file: any ``*.org`` in an ``org/`` folder, or a GTD
    file by name (inbox.org, next_actions.org, ...);
  * Bash that writes one as raw text: a redirection (``>``, ``>>``, ``&>``),
    ``tee``, ``sed -i`` / ``perl -i``, or ``cp`` / ``mv`` / ``dd of=`` /
    ``truncate`` onto it.

The task tools themselves (``python3 .datacore/lib/org_workspace_adapter.py
... --file x/org/inbox.org``) pass: naming a task file is not writing it.
Other ``.org`` files (notes, not tasks) get the old advisory only.

Registration (the owner registers hooks): Edit|Write is registered in
.claude/settings.json. The Bash half needs its own entry there:

    {"matcher": "Bash", "hooks": [{"type": "command",
      "command": "python3 ~/Data/.datacore/lib/org_guard.py"}]}

Fires in ~1 ms on anything that is not a task file. On unreadable input it
refuses nothing and says so (a broken hook must not stop all work).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

GTD_FILES = {"inbox.org", "next_actions.org", "someday.org", "waiting.org",
             "projects.org", "research_learning.org", "nightshift.org"}

HOW = ("Use the task tools instead:\n"
       "  python3 .datacore/lib/org_workspace_adapter.py add --file <file> --heading <title> [--tags a,b]\n"
       "  python3 .datacore/lib/org_workspace_adapter.py update|complete|move --file <file> --id <id> ...\n"
       "or the GTD MCP tools (datacore.gtd.*). New tasks go to inbox.org first.")

GTD_WARNING = (
    "[Datacore GTD Guard]\n\n"
    "You are about to directly edit an org-mode file. "
    "Org-mode files are managed by the GTD pipeline.\n\n" + HOW)

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n", "|&"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
EDIT_IN_PLACE = {"sed", "gsed", "perl"}
COPY_ONTO = {"cp", "mv", "install", "rsync", "ln"}


def is_task_file(path: str) -> bool:
    """A task file: ``*.org`` whose folder is ``org/``, or a GTD file by name."""
    p = path.strip().strip("'\"")
    if not p.endswith(".org"):
        return False
    parts = re.split(r"[\\/]+", p)
    return os.path.basename(p) in GTD_FILES or (len(parts) >= 2 and parts[-2] == "org")


def _segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    segs, cur = [], []
    for tok in tokens:
        if tok in SEPARATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def shell_write_target(command: str) -> str | None:
    """The task file a shell command writes as raw text, or None."""
    try:
        segs = _segments(command)
    except ValueError:
        # Unbalanced quotes: fall back to the plain redirection shape.
        m = re.search(r">>?\|?\s*([^\s;&|<>]+\.org)\b", command)
        return m.group(1) if m and is_task_file(m.group(1)) else None
    for seg in segs:
        # Redirections: shlex splits `2>>` into `2`, `>>`; `>&2` targets a fd.
        for i, tok in enumerate(seg[:-1]):
            if tok in REDIRECTS or (tok.endswith(">") and set(tok) <= set("<>&|0123456789")):
                if is_task_file(seg[i + 1]):
                    return seg[i + 1]
        words = [t for t in seg if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)]
        if not words:
            continue
        # sudo / env / command prefixes do not change what runs.
        while words and os.path.basename(words[0]) in ("sudo", "env", "command", "nohup", "time"):
            words = words[1:]
        if not words:
            continue
        name, args = os.path.basename(words[0]), words[1:]
        targets = [a for a in args if is_task_file(a)]
        if not targets:
            continue
        if name == "tee":
            return targets[0]
        if name in EDIT_IN_PLACE and any(a.startswith("--in-place") or re.match(r"^-[A-Za-z]*i", a)
                                         for a in args):
            return targets[0]
        if name in COPY_ONTO and args and is_task_file(args[-1]):
            return args[-1]
        if name == "truncate":
            return targets[0]
    if re.search(r"\bdd\b[^;&|]*\bof=([^\s;&|]+)", command):
        m = re.search(r"\bof=([^\s;&|]+)", command)
        if m and is_task_file(m.group(1)):
            return m.group(1)
    return None


def _deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)


def main() -> int:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        json.dump({"additionalContext": "[Datacore GTD Guard] Warning: could not parse hook "
                   "input. Task files change only through org_workspace_adapter.py."}, sys.stdout)
        return 0

    tool = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input") or {}

    if tool == "Bash":
        target = shell_write_target(str(tool_input.get("command", "")))
        if target:
            _deny(f"[Datacore GTD Guard] Refused: this shell command writes the task file "
                  f"{target} as raw text. Tasks change only through the task tools (MEM-38).\n\n"
                  + HOW)
        return 0

    file_path = str(tool_input.get("file_path", ""))
    if not file_path.endswith(".org"):
        return 0
    if is_task_file(file_path):
        _deny(f"[Datacore GTD Guard] Refused: {os.path.basename(file_path)} is a task file. "
              f"Tasks change only through the task tools, never by {tool or 'a raw edit'} "
              f"(MEM-38).\n\n" + HOW)
        return 0
    json.dump({"additionalContext": GTD_WARNING}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
