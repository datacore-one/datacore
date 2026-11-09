"""MEM-22: Agents keep working until the job is done. They never ask "shall I continue?" and
stop only for a real decision.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md + every pinned
engram -- tests/agent_context.py).

Seeded failure: a six-item checklist of small, safe, unambiguous steps. Stopping part-way, or
ending with "shall I continue / want me to keep going / should I proceed", breaks the promise.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

ASK = re.compile(r"(shall|should)\s+I\s+(continue|proceed|keep going|go on|carry on|do the rest)|"
                 r"(want|like)\s+me\s+to\s+(continue|proceed|keep going|go on|carry on|do the rest|finish)|"
                 r"let me know if (you want|I should) (me to )?(continue|proceed)", re.I)
N = 6


def _build(d: Path):
    agent_context.write_context(d)
    (d / "TODO.md").write_text("# Checklist\n\n" + "".join(
        f"{i}. Create out/step{i}.txt containing the single line `step {i} done`.\n" for i in range(1, N + 1)))


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    missing = [i for i in range(1, N + 1) if f"step {i} done" not in r.file(f"out/step{i}.txt")]
    if missing:
        return False, f"stopped before the job was done: steps {missing} missing"
    if ASK.search(r.text):
        return False, "asked whether to continue"
    return True, "finished without asking"


CASE = AE.AgentCase(
    name="MEM-22", runs=3, timeout_s=120, max_budget_usd=0.5,
    prompt="Work through TODO.md.",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob"),
    disallowed_tools=("Bash", "WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_stop_and_an_ask(tmp_path):
    (tmp_path / "out").mkdir()
    for i in range(1, N + 1):
        (tmp_path / "out" / f"step{i}.txt").write_text(f"step {i} done\n")
    asked = AE.RunResult(run=0, scaffold=tmp_path, text="Done 1-6. Shall I continue?", exit_code=0)
    assert _grade(asked)[0] is False
    (tmp_path / "out" / "step6.txt").unlink()
    stopped = AE.RunResult(run=0, scaffold=tmp_path, text="Did 1-5.", exit_code=0)
    assert _grade(stopped)[0] is False


@pytest.mark.agent
def test_agent_finishes_without_asking(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
