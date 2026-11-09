"""TSK-7: Deadlines and scheduled dates appear in my agenda, and anything
overdue is flagged rather than slipping by silently.

Kind: deterministic. The core adapter's `agenda` and `deadlines` commands
(what the GTD MCP tools agenda_view / deadline_warnings call) on a tmp file
with dates relative to today.

Seeded failure: open tasks scheduled today, scheduled three days ago,
with a deadline in two days and one five days ago, plus finished tasks with
past dates. The dated open tasks must all show; the two past ones must be
flagged overdue; finished tasks must not show. Verified red by dropping the
overdue flag (deadlines) and by the agenda's today-onwards window, which
silently drops a scheduled task the day after its date.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ADAPTER = LIB / "org_workspace_adapter.py"
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"


def _stamp(d: date) -> str:
    return f"<{d.isoformat()} {d.strftime('%a')}>"


@pytest.fixture
def run(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    t = date.today()
    rows = [  # (state, title, planning line)
        ("TODO", "Scheduled today", f"SCHEDULED: {_stamp(t)}"),
        ("NEXT", "Scheduled three days ago", f"SCHEDULED: {_stamp(t - timedelta(days=3))}"),
        ("TODO", "Deadline in two days", f"DEADLINE: {_stamp(t + timedelta(days=2))}"),
        ("WAITING", "Deadline five days ago", f"DEADLINE: {_stamp(t - timedelta(days=5))}"),
        ("DONE", "Finished, was scheduled today", f"SCHEDULED: {_stamp(t)}"),
        ("DONE", "Finished, deadline passed", f"DEADLINE: {_stamp(t - timedelta(days=5))}"),
    ]
    f = tmp_path / "5-evals" / "org" / "next_actions.org"
    f.parent.mkdir(parents=True)
    f.write_text(HEADER + "".join(
        f"* {s} {title}\n{plan}\n:PROPERTIES:\n:ID: org-t7-{i}\n:END:\n"
        for i, (s, title, plan) in enumerate(rows)), encoding="utf-8")

    def _run(cmd: str, days: int) -> dict:
        r = subprocess.run([sys.executable, str(ADAPTER), cmd, "--file", str(f), "--days", str(days)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)
    return _run


def _title(task: dict) -> str:
    return task.get("heading") or task.get("title") or ""


def test_scheduled_work_is_in_the_agenda_and_a_missed_date_is_flagged(run):
    out = run("agenda", 7)
    by = {_title(t): t for t in out["tasks"]}
    assert "Scheduled today" in by
    assert "Scheduled three days ago" in by, (
        "a task scheduled three days ago and still open dropped out of the agenda silently")
    assert by["Scheduled three days ago"].get("overdue") is True, "a missed scheduled date is not flagged"
    assert not by["Scheduled today"].get("overdue")
    assert "Finished, was scheduled today" not in by, "a finished task is still on the agenda"


def test_deadlines_are_listed_and_overdue_ones_flagged(run):
    out = run("deadlines", 14)
    by = {_title(t): t for t in out["tasks"]}
    assert set(by) == {"Deadline in two days", "Deadline five days ago"}, sorted(by)
    assert by["Deadline five days ago"]["overdue"] is True
    assert by["Deadline in two days"]["overdue"] is False
    assert out["overdue"] == 1
