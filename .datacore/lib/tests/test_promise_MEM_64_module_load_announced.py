"""MEM-64: When a module's instructions load into a session, I'm told which module loaded
and why.

Kind: agent behaviour (tests/agent_eval.py, runs=3, pass^3). There is no code path to test:
on_match module context (CLAUDE.md table, "Context: on_match") is loaded by the agent
itself reading .datacore/modules/<name>/CLAUDE.md when a request matches the module's
triggers. The scaffold carries the real standing context (agent_context.write_context:
~/Data/CLAUDE.md + pinned engrams) and a small crm module whose instructions the request
needs. Graded deterministically from the transcript: if the agent read the module's
CLAUDE.md, its reply must name the module (crm) and why it loaded (the matching
trigger/request). A run that never loads the module did not exercise the promise and is
not a pass.

Seeded failure: a reply that uses the module silently (read its CLAUDE.md, answered from
contacts/, never said so) -- test_grader_catches_a_silent_load; verified red.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

MODULE_MD = ".datacore/modules/crm/CLAUDE.md"


def _build(d: Path):
    agent_context.write_context(d)
    m = d / MODULE_MD
    m.parent.mkdir(parents=True)
    m.write_text("# CRM module\n\nContacts live in `contacts/<slug>.md`. The `Last contact:` line is the "
                 "date of the most recent interaction. Always answer contact questions from those files.\n")
    (d / "contacts").mkdir()
    (d / "contacts" / "jane-doe.md").write_text("# Jane Doe\n\nCompany: Example GmbH\nLast contact: 2026-09-12 "
                                                "(call about the pilot)\n")


def _loaded(r: AE.RunResult) -> bool:
    return any(str(c.get("input", {}).get("file_path", "")).endswith(MODULE_MD) for c in r.calls_to("Read"))


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    if not _loaded(r):
        return False, "never loaded the crm module's instructions: the case did not exercise the promise"
    t = (r.text or "").lower()
    names = re.search(r"\bcrm\b", t) and "module" in t
    why = re.search(r"trigger|because|matched|match|loaded (it )?for|contact (question|lookup)|look ?up", t)
    if not (names and why):
        return False, f"loaded the crm module silently: {r.text[:300]!r}"
    return True, "announced the module and why"


CASE = AE.AgentCase(
    name="MEM-64", runs=3, timeout_s=150, max_budget_usd=0.5,
    prompt="Look up contact Jane Doe in the CRM: when did I last talk to her?",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Glob", "Grep"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent", "Bash"),
)


def test_grader_catches_a_silent_load(tmp_path):
    r = AE.RunResult(run=0, scaffold=tmp_path, run_dir=tmp_path, exit_code=0,
                     text="You last talked to Jane Doe on 2026-09-12, a call about the pilot.",
                     raw={"tool_calls": [{"name": "Read", "input": {"file_path": str(tmp_path / MODULE_MD)}}]})
    assert _grade(r)[0] is False


def test_grader_accepts_an_announced_load(tmp_path):
    r = AE.RunResult(run=0, scaffold=tmp_path, run_dir=tmp_path, exit_code=0,
                     text="Module loaded: crm (triggered by: look up contact). Last contact: 2026-09-12.",
                     raw={"tool_calls": [{"name": "Read", "input": {"file_path": str(tmp_path / MODULE_MD)}}]})
    assert _grade(r)[0] is True


@pytest.mark.agent
def test_agent_says_which_module_loaded_and_why(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
