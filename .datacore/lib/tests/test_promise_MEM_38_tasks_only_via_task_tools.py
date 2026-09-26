"""MEM-38: Tasks are changed only through the task tools, never by editing
task files as raw text.

Kind: deterministic. The enforcement is the PreToolUse org guard that
.claude/settings.json registers (.datacore/lib/org_guard.py). A rule the agent
may ignore is a convention, not a guard (owner decision: tasks change only
through org_workspace_adapter / the GTD tools). So:
  * an Edit or Write of a task file (<space>/org/*.org) is REFUSED by the hook
    (exit 2, or permissionDecision "deny") -- a warning the agent can read
    past does not keep the promise;
  * a raw shell write to a task file (echo >> inbox.org, sed -i on
    next_actions.org) is refused by a hook registered for Bash;
  * the task tools themselves (python3 .datacore/lib/org_workspace_adapter.py ...)
    and edits of non-org files pass.

Seeded failure: the guard reduced to an advisory (additionalContext, exit 0) --
today's behaviour.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"


def _hooks_for(tool: str) -> list[str]:
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    out = []
    for entry in cfg.get("hooks", {}).get("PreToolUse", []):
        import re
        if re.fullmatch(entry.get("matcher", ""), tool) or entry.get("matcher") in ("", "*"):
            out += [h["command"] for h in entry.get("hooks", []) if h.get("type") == "command"]
    return out


def _refused(tool: str, tool_input: dict) -> tuple[bool, str]:
    """Run every PreToolUse hook registered for `tool`; refused if any blocks."""
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT),
                          "hook_event_name": "PreToolUse", "session_id": "mem38-eval"})
    notes = []
    for cmd in _hooks_for(tool):
        if "org" not in cmd and "task" not in cmd and "gtd" not in cmd:
            continue   # only the task-file guards; unrelated hooks may have side effects
        p = subprocess.run(["bash", "-c", cmd.replace("~", str(Path.home()))], input=payload,
                           capture_output=True, text=True, timeout=30, env={**os.environ})
        out = p.stdout.strip()
        decision = ""
        try:
            j = json.loads(out) if out else {}
            decision = (j.get("hookSpecificOutput") or {}).get("permissionDecision", "") or j.get("decision", "")
        except ValueError:
            pass
        notes.append(f"{cmd.split('/')[-1]}: rc={p.returncode} decision={decision or '-'}")
        if p.returncode == 2 or decision in ("deny", "block"):
            return True, "; ".join(notes)
    return False, "; ".join(notes) or "no task-file hook registered"


@pytest.mark.parametrize("tool,tool_input", [
    ("Edit", {"file_path": str(ROOT / "0-personal/org/next_actions.org"),
              "old_string": "* TODO Call the bank", "new_string": "* DONE Call the bank"}),
    ("Write", {"file_path": str(ROOT / "0-personal/org/inbox.org"), "content": "* Inbox\n** TODO x\n"}),
])
def test_raw_edit_of_a_task_file_is_refused(tool, tool_input):
    refused, why = _refused(tool, tool_input)
    assert refused, f"{tool} on {Path(tool_input['file_path']).name} was not refused ({why})"


@pytest.mark.parametrize("command", [
    "echo '** TODO Pay the invoice' >> 0-personal/org/inbox.org",
    "sed -i '' 's/TODO Call the bank/DONE Call the bank/' 0-personal/org/next_actions.org",
])
def test_raw_shell_write_to_a_task_file_is_refused(command):
    refused, why = _refused("Bash", {"command": command})
    assert refused, f"shell write to a task file was not refused: {command!r} ({why})"


@pytest.mark.parametrize("tool,tool_input", [
    ("Bash", {"command": "python3 .datacore/lib/org_workspace_adapter.py add --file 0-personal/org/inbox.org --heading x"}),
    ("Edit", {"file_path": str(ROOT / "0-personal/notes/journals/2026-09-26.md"), "old_string": "a", "new_string": "b"}),
])
def test_task_tools_and_non_task_files_pass(tool, tool_input):
    refused, why = _refused(tool, tool_input)
    assert not refused, f"the guard blocks legitimate work: {tool} {tool_input} ({why})"
