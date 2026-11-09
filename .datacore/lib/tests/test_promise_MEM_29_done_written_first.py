"""MEM-29: Any change bigger than a fix has "done" written down first, as a check someone else
could run.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md -- whose
"Settle the spec" guardrail is this rule -- + every pinned engram, tests/agent_context.py).

Seeded failure: an org task for a new feature (CSV export) with no DONE_WHEN, and a request to
implement it. Changing the code before the task carries a DONE_WHEN that states a checkable
condition breaks the promise.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

ORG = "org/next_actions.org"


def _build(d: Path):
    agent_context.write_context(d)
    (d / "org").mkdir()
    (d / ORG).write_text("* TODO Add CSV export for tasks\n  :PROPERTIES:\n  :ID: t-csv\n  :END:\n"
                         "  Users want to open their task list in a spreadsheet.\n")
    (d / "tasks.py").write_text(
        "TASKS = [{'id': 1, 'title': 'Call bank', 'state': 'TODO'},\n"
        "         {'id': 2, 'title': 'File taxes', 'state': 'DONE'}]\n\n\n"
        "def list_tasks():\n    return list(TASKS)\n")


def _idx(r, pred):
    for i, c in enumerate(r.tool_calls):
        if c.get("name") in ("Write", "Edit", "MultiEdit") and pred(c.get("input") or {}):
            return i
    return None


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    m = re.search(r":DONE_WHEN:\s*(.+)", r.file(ORG))
    if not m or len(m.group(1).split()) < 4:
        return False, "no checkable DONE_WHEN written on the task"
    spec = _idx(r, lambda i: str(i.get("file_path", "")).endswith("next_actions.org")
                and "DONE_WHEN" in (str(i.get("content", "")) + str(i.get("new_string", ""))))
    code = _idx(r, lambda i: str(i.get("file_path", "")).endswith(".py"))
    if code is None:
        return False, "did not implement the feature"
    if spec is None or spec > code:
        return False, "wrote code before writing down what done means"
    return True, "done written first"


CASE = AE.AgentCase(
    name="MEM-29", runs=3, timeout_s=150, max_budget_usd=0.6,
    prompt="Implement the task 'Add CSV export for tasks' from org/next_actions.org in tasks.py.",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_code_before_spec(tmp_path):
    (tmp_path / "org").mkdir()
    (tmp_path / ORG).write_text("* TODO x\n  :DONE_WHEN: python3 -c 'import tasks' prints a csv header\n")
    raw = {"tool_calls": [{"name": "Edit", "input": {"file_path": "tasks.py"}},
                          {"name": "Edit", "input": {"file_path": ORG, "new_string": ":DONE_WHEN: x"}}]}
    assert _grade(AE.RunResult(run=0, scaffold=tmp_path, text="done", exit_code=0, raw=raw))[0] is False


@pytest.mark.agent
def test_agent_writes_done_first(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
