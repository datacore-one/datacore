"""/today renders the one briefing; it never writes its own (2026-10-01).

Three things could produce the morning briefing: the box (cos_morning ->
cos_generate -> datacore-app cos_reasoning.generate()), the app's fallback
(the same generate()), and /today, whose model composed `## Daily Briefing`
itself from steps 3-14. The stale-task rule (an old scheduled decision is not
today's decision) and the PR-count check (a count of PRs to merge traces to
today's GitHub triage) are code in generate(). A briefing /today composed went
past both: the same morning could tell the owner to decide a release that had
shipped and to merge "12 green PRs" when the triage named one.

The contract: generate() is the one generator. /today puts the briefing into
the journal by running chief-of-staff's cos_journal.py, which renders that
generator's artifact, and generates it through generate() when none exists.
No step of this file tells the model to compose or write the section.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
TODAY = ROOT / ".datacore" / "commands" / "today.md"
COS_JOURNAL = ".datacore/modules/chief-of-staff/server/lib/cos_journal.py"


def _text() -> str:
    return TODAY.read_text(encoding="utf-8")


def _step(n: str) -> str:
    m = re.search(rf"^## Step {n}:.*?(?=^## )", _text(), re.S | re.M)
    assert m, f"today.md has no Step {n}"
    return m.group(0)


def test_the_journal_step_renders_the_generators_artifact():
    step = _step("16")
    assert COS_JOURNAL in step
    assert "cos_reasoning.generate" in step


def test_no_step_composes_the_briefing_section():
    text = _text()
    for phrase in (r"Compose the briefing sections",
                   r"Create with frontmatter \+ briefing",
                   r"Replace in-place with fresh content",
                   r"Proceed with full briefing generation"):
        assert not re.search(phrase, text), f"today.md still says: {phrase}"


def test_a_missing_briefing_is_generated_by_the_one_generator():
    step = _step("2")
    assert COS_JOURNAL in step or "Step 16" in step
    assert "cos_reasoning.generate" in _text()
    assert not re.search(r"full briefing generation", step)


def test_the_command_says_which_generator_is_canonical_and_why():
    text = _text()
    m = re.search(r"^### One generator\s*$(.*?)(?=^### |^## )", text, re.S | re.M)
    assert m, "today.md has no '### One generator' section"
    body = m.group(1)
    assert "cos_reasoning.generate" in body
    assert re.search(r"stale", body, re.I) and re.search(r"PR", body)
