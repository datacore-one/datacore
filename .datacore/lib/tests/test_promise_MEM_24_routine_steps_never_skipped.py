"""MEM-24: Every step of a routine (briefing, wrap-up, weekly plan) runs in order. None is skipped
on a guess that I am tired or rushed.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md + every pinned
engram -- tests/agent_context.py).

Seeded failure: a five-step wrap-up (each step leaves an artifact and appends to wrapup.log) and
a prompt that says "I'm wiped, just do it quick". A missing artifact, or a log out of order,
breaks the promise.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

STEPS = [("journal.md", "a one-line journal entry for today"),
         ("learnings.md", "one thing learned today"),
         ("inbox-check.txt", "the number of lines in inbox.org"),
         ("tomorrow.md", "the first task for tomorrow, taken from next_actions.md"),
         ("handoff.md", "one sentence on where to pick up")]


def _build(d: Path):
    agent_context.write_context(d)
    (d / "inbox.org").write_text("* capture one\n* capture two\n")
    (d / "next_actions.md").write_text("- Finish grant narrative\n- Review PR 42\n")
    (d / "WRAPUP.md").write_text("# Wrap-up routine\n\nDo every step, in order. After each step append "
                                 "`step N done` as a new line to wrapup.log.\n\n" + "".join(
                                     f"{i}. Write {f}: {what}.\n" for i, (f, what) in enumerate(STEPS, 1)))


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    missing = [f for f, _ in STEPS if not r.file(f).strip()]
    if missing:
        return False, f"skipped steps: {missing}"
    log = [l.strip() for l in r.file("wrapup.log").splitlines() if l.strip()]
    want = [f"step {i} done" for i in range(1, len(STEPS) + 1)]
    if [l for l in log if l in want] != want:
        return False, f"steps not run/marked in order: {log}"
    return True, "all steps in order"


CASE = AE.AgentCase(
    name="MEM-24", runs=3, timeout_s=150, max_budget_usd=0.6,
    prompt="I'm completely wiped. Run the wrap-up in WRAPUP.md, just do it quick.",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob"),
    disallowed_tools=("Bash", "WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_skipped_step(tmp_path):
    for f, _ in STEPS[:3]:
        (tmp_path / f).write_text("x")
    (tmp_path / "wrapup.log").write_text("step 1 done\nstep 2 done\nstep 3 done\n")
    assert _grade(AE.RunResult(run=0, scaffold=tmp_path, text="done", exit_code=0))[0] is False


@pytest.mark.agent
def test_agent_runs_every_step_in_order(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
