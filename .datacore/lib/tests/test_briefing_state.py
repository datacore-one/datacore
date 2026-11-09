"""/today step 2 must not take a withheld stub for a finished briefing.

2026-09-30: Winston's writer draft was withheld (numbers without a source) and
the journal got a `## Daily Briefing` holding only a raw "Facts" list. Step 2
checked for the heading alone, answered EXISTS, and would have skipped straight
to standups; the owner would have had no briefing. A briefing is complete only
when it carries its narrative sections.
"""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import briefing_state as bs  # noqa: E402

FULL = "---\ndate: x\n---\n\n## Daily Briefing\n\n### Good Morning\nhi\n\n### The World\nw\n\n### Your Agenda\na\n"
STUB = ("---\ndate: x\n---\n\n## Daily Briefing\n\n**Plain facts this morning: the written briefing quoted figures no source holds**\n\n"
        "### What needs you: still failing\n\n### Facts\n\n• the_ask.total: 21\n\n### Data's Observation\n\n"
        "The writer's draft contained numbers that do not trace to any data it was given, so it was withheld.\n")


def test_states(tmp_path):
    f = tmp_path / "2026-10-01.md"
    assert bs.briefing_state(f) == "NO_FILE"
    f.write_text("---\ndate: x\n---\n\n## Session: something\n")
    assert bs.briefing_state(f) == "NO_BRIEFING"
    f.write_text(FULL)
    assert bs.briefing_state(f) == "EXISTS"
    f.write_text(STUB)
    assert bs.briefing_state(f) == "STUB"


def test_only_the_briefing_section_counts(tmp_path):
    """A 'Good Morning' heading in a later session section does not make a stub complete."""
    f = tmp_path / "d.md"
    f.write_text(STUB + "\n## Session: notes\n\n### Good Morning\n### The World\n")
    assert bs.briefing_state(f) == "STUB"


def test_cli_prints_the_state(tmp_path):
    f = tmp_path / "d.md"; f.write_text(STUB)
    out = subprocess.run([sys.executable, str(LIB / "briefing_state.py"), str(f)], capture_output=True, text=True)
    assert out.stdout.strip() == "STUB"


def test_today_step_2_uses_it():
    today = (LIB.parent / "commands" / "today.md").read_text(encoding="utf-8")
    step2 = today.split("## Step 2:")[1].split("## Step 3:")[0]
    assert "briefing_state.py" in step2 and "STUB" in step2
