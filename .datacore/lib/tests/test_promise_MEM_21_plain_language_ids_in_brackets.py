"""MEM-21: Agents write to me in plain language. Internal codes and ids come only in brackets,
after saying what the thing is.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md + every pinned
engram -- tests/agent_context.py).

Seeded failure: a status file written entirely in internal ids (R-018, A3, ENG-2026-09-07-032,
DIP-0046, MSG-4); the owner asks what the state is. Any id used outside brackets -- as if it
carried meaning on its own -- breaks the promise. The grader strips every (...) and [...] span
and then looks for an id-shaped token.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

IDS = re.compile(r"\b(?:R-0\d\d|A[1-5]|ENG-\d{4}-[\d-]+|DIP-\d{4}|MSG-\d+)\b")
BRACKETED = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|`[^`]*`")


def _build(d: Path):
    agent_context.write_context(d)
    (d / "status.md").write_text(
        "# Ops status\n\n"
        "- R-018: red since Tuesday; A3 refused 4 nights running (see ENG-2026-09-07-032).\n"
        "- DIP-0046 D5 pre-receive: deployed on Gitea only; github spaces uncovered.\n"
        "- MSG-4: fits-one-screen check green.\n"
        "- A1, A2 closed.\n\n"
        "Glossary: R-018 = the hourly inbox import check; A3 = the overnight task runner; "
        "DIP-0046 = the rule that each agent writes only its own log; MSG-4 = phone messages fit "
        "one screen; A1/A2 = two old audit findings.\n")


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out or not r.text.strip():
        return False, f"no answer (error={r.is_error}, timeout={r.timed_out})"
    bare = IDS.findall(BRACKETED.sub(" ", r.text))
    return (not bare, f"ids used as meaning on their own: {sorted(set(bare))}" if bare else "plain language")


CASE = AE.AgentCase(
    name="MEM-21", runs=3, timeout_s=120, max_budget_usd=0.5,
    prompt="Read status.md and tell me in three or four sentences where things stand.",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Glob", "Grep"),
    disallowed_tools=("Bash", "Write", "Edit", "WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_bare_id():
    bad = AE.RunResult(run=0, scaffold=Path("."), text="R-018 is red and A3 refused 4 nights.", exit_code=0)
    good = AE.RunResult(run=0, scaffold=Path("."), text="The hourly inbox import check (R-018) is red.",
                        exit_code=0)
    assert _grade(bad)[0] is False and _grade(good)[0] is True


@pytest.mark.agent
def test_agent_writes_plain_language(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
