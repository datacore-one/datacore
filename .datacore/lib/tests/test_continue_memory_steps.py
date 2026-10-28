"""continue.md uses PLUR memory both ways (owner's design, 2026-10-28).

Resume recalls twice: once for the task at hand, once for lessons from
similar work in other projects. Save writes engrams for what the session
learned, never the task's status, which stays in the ledger task.
"""

import re
from pathlib import Path

DOC = (Path(__file__).parent.parent.parent / "commands" / "continue.md").read_text()


def _section(start: str, end: str) -> str:
    return DOC[DOC.index(start):DOC.index(end)]


def test_resume_recalls_for_the_task_then_for_lessons_elsewhere():
    resume = _section("### 1. Resume Mode", "## Prioritization Logic")
    recalls = re.findall(r"plur_recall_hybrid", resume)
    assert len(recalls) >= 2, "resume must recall twice"
    assert "task at hand" in resume
    assert "other projects" in resume


def test_save_writes_scoped_engrams_not_status():
    for start, end in (("### 0. Save Mode", "#### Focus Mode Override (Save)"),
                       ("### 0b. Inline Save Mode", "### 1. Resume Mode")):
        save = _section(start, end)
        assert "plur_learn" in save, start
        assert "scope" in save, start
        assert "never task status" in save.lower(), start
