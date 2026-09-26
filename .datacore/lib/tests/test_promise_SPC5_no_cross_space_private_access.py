"""SPC-5: An agent working in one space cannot read or change another space's
private data unless that space allows it.

Kind: deterministic. The real PreToolUse guard every Claude Code session in
~/Data runs (`hooks/space_policy_guard.py`, registered for Bash|Edit|Write|Read),
fed the tool-call payload a session would send, against a tmp install
(DATACORE_ROOT) with two spaces:
  * 0-personal -- personal space (marker type `personal`), holding a private
    journal;
  * 1-datafund -- a team space, where the agent is working (session cwd in
    1-datafund/2-projects/verity).

Promise, as evals:
  * the agent can read and edit its own space (allowed, no deny);
  * a Read of 0-personal's journal from a 1-datafund session is denied;
  * an Edit/Write of 0-personal's files from a 1-datafund session is denied;
  * a shell command from that session reading 0-personal's journal is denied.
("unless that space allows it": no space here allows it, so every crossing
must be refused.)

Seeded failure: today's policy keys on the TARGET space's type only
(`policies.<type>.<category>`, default allow) and never compares it with the
space the session is working in -- so personal data is readable and writable
from any team-space session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "space_policy_guard.py"


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "Data"
    for name, kind in (("0-personal", "personal"), ("1-datafund", "team")):
        sp = root / name
        (sp / ".datacore").mkdir(parents=True)
        (sp / "org").mkdir()
        (sp / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name.split('-', 1)[1]}\n  type: {kind}\n")
    (root / "0-personal" / "notes" / "journals").mkdir(parents=True)
    (root / "0-personal" / "notes" / "journals" / "2026-09-26.md").write_text("private\n")
    (root / "1-datafund" / "2-projects" / "verity").mkdir(parents=True)
    (root / "1-datafund" / "2-projects" / "verity" / "parser.py").write_text("x = 1\n")
    return root


def _call(root: Path, tool: str, tool_input: dict, cwd: Path) -> tuple[bool, str]:
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd),
               "hook_event_name": "PreToolUse", "session_id": "spc5"}
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=30, env={**os.environ, "DATACORE_ROOT": str(root)})
    out = r.stdout.strip()
    denied = r.returncode == 2
    if out.startswith("{"):
        try:
            d = json.loads(out)
            spec = d.get("hookSpecificOutput") or {}
            denied = denied or spec.get("permissionDecision") == "deny" or d.get("decision") == "block"
        except ValueError:
            pass
    return denied, out + r.stderr


def _session(root: Path) -> Path:
    return root / "1-datafund" / "2-projects" / "verity"


def test_the_agent_may_work_in_its_own_space(install):
    cwd = _session(install)
    own = cwd / "parser.py"
    for tool, inp in (("Read", {"file_path": str(own)}),
                      ("Edit", {"file_path": str(own), "old_string": "1", "new_string": "2"})):
        denied, out = _call(install, tool, inp, cwd)
        assert not denied, f"{tool} of its own space was denied: {out}"


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "0-personal/notes/journals/2026-09-26.md"}),
    ("Edit", {"file_path": "0-personal/notes/journals/2026-09-26.md", "old_string": "private", "new_string": "x"}),
    ("Write", {"file_path": "0-personal/org/inbox.org", "content": "* TODO planted\n"}),
])
def test_another_spaces_private_data_is_refused(install, tool, inp):
    inp = {**inp, "file_path": str(install / inp["file_path"])}
    denied, out = _call(install, tool, inp, _session(install))
    assert denied, f"a 1-datafund session was allowed to {tool} {inp['file_path']}: {out or 'no output'}"


def test_a_shell_command_inside_another_space_is_refused(install):
    target = install / "0-personal" / "notes" / "journals" / "2026-09-26.md"
    denied, out = _call(install, "Bash", {"command": f"cat {target}"}, _session(install))
    assert denied, f"a shell command inside 0-personal was allowed: {out or 'no output'}"
