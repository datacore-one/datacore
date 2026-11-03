"""/tomorrow has an explicit unattended mode (plan 2026-10-31, phase H, evening-handoff 0).

The evening job runs /tomorrow with no one to answer, yet the command asked
"Confirm queue for nightshift? [Y/n]" and "Add more tasks to delegate?". Whether
an unattended run moved anything, or marked what it made as agent-made, could
not be told from the code. The mode is now stated: no prompt, every task the run
creates carries :ORIGIN: agent (what the delegation gate keys on, nightshift
task_queue MACHINE_ORIGINS), and the owner's own tasks keep their origin.
Written before the change.
"""
from pathlib import Path

CMD = Path(__file__).resolve().parents[2] / "commands" / "tomorrow.md"


def _section():
    text = CMD.read_text()
    assert "## Unattended mode" in text, "tomorrow.md states its unattended mode"
    start = text.index("## Unattended mode")
    end = text.find("\n## ", start + 5)
    return text[start:end]


def test_the_mode_is_entered_by_its_argument():
    assert "`unattended`" in _section()


def test_no_prompt_is_asked():
    s = _section().lower()
    assert "never ask" in s and "[y/n]" in s


def test_every_task_it_creates_is_agent_made():
    assert ":ORIGIN: agent" in _section()


def test_the_owners_tasks_keep_their_origin():
    s = _section()
    assert "never change" in s.lower() and "ORIGIN" in s


def test_the_mode_comes_before_the_steps():
    text = CMD.read_text()
    assert text.index("## Unattended mode") < text.index("## Step 0")
