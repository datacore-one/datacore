"""TSK-10: A sprint that has ended is closed properly (each item done, carried
over or dropped with a reason), and any drift is shown to me.

Kind: deterministic (the real sprint_files.discover + health on a tmp space,
PR lookups faked, and sprint_validate on broken files) plus a production
contract (read-only: no sprint in any live space is still open past its end).
Eval only; closing sprints belongs to another session.

Seeded failure (GT-12/13/14): a sprint still active after its end date, one
left in `review` after its end, a closed sprint holding an untouched item that
was never carried, an item dropped with no reason, an in-flight item in a closed
sprint, a `review` item whose PR merged, and a PR lookup that fails. Each must
be named by health(); a broken sprint file must fail validation. Verified red
by making health() skip expired sprints.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parent.parent
ROOT = LIB.parent.parent
sys.path.insert(0, str(LIB))

import sprint_files  # noqa: E402

TODAY = date(2026, 9, 26)


def _sprint(base: Path, sid: str, status: str, end: str, backlog: list, carryover=None):
    d = base / "5-evals" / "1-tracks" / "dev" / "sprints" / sid
    d.mkdir(parents=True)
    (d / "sprint.yaml").write_text(yaml.safe_dump({
        "sprint_id": sid, "status": status, "dates": {"start": "2026-09-01", "end": end},
        "backlog": backlog, "carryover": carryover or []}))


def _pr(repo: str, n: int) -> dict:
    if n == 9:
        raise RuntimeError("HTTP 502")
    return {"state": "MERGED", "mergedAt": "2026-06-09T10:00:00Z"} if n == 1 else {"state": "OPEN"}


@pytest.fixture
def problems(tmp_path):
    _sprint(tmp_path, "W36-active-past-end", "active", "2026-09-06", [
        {"id": "A1", "state": "todo"}])
    _sprint(tmp_path, "W37-stuck-in-review", "review", "2026-09-13", [
        {"id": "R1", "state": "done"}])
    _sprint(tmp_path, "W23-closed", "closed", "2026-06-10", [
        {"id": "B1", "state": "review", "pr": "https://github.com/o/r/pull/1"},
        {"id": "B2", "state": "todo"},
        {"id": "B3", "state": "dropped"},
        {"id": "B4", "state": "done"},
        {"id": "B5", "state": "dropped", "reason": "superseded by the ledger rewrite"},
        {"id": "B6", "state": "todo"},
    ], carryover=["B6"])
    _sprint(tmp_path, "W39-running", "active", "2026-10-02", [
        {"id": "C1", "state": "in-progress", "pr": "https://github.com/o/r/pull/9"},
        {"id": "C2", "state": "review", "pr": "https://github.com/o/r/pull/2"}])
    disc = sprint_files.discover("5-evals", fetch=False, root=tmp_path)
    return "\n".join(sprint_files.health(disc, today=TODAY, pr_lookup=_pr))


def test_a_sprint_still_active_after_its_end_is_named(problems):
    assert "W36-active-past-end" in problems


def test_a_sprint_left_in_review_after_its_end_is_named(problems):
    assert "W37-stuck-in-review" in problems, (
        "a sprint that ended two weeks ago and never reached `closed` is not reported")


def test_a_closed_sprint_names_every_item_not_done_carried_or_dropped_with_reason(problems):
    missing = [i for i in ("W23-closed#B1", "W23-closed#B2", "W23-closed#B3") if i not in problems]
    assert not missing, f"items left open in a closed sprint are not shown: {missing}\n{problems}"
    for fine in ("#B4", "#B5", "#B6"):
        assert f"W23-closed{fine}" not in problems, f"{fine} was closed properly but is reported"


def test_merged_work_and_an_unchecked_pr_are_shown(problems):
    assert "W23-closed#B1" in problems and "merged" in problems
    assert "UNVERIFIED" in problems, "a failed PR lookup is not reported as unverified"
    assert "W39-running#C2" not in problems, "an open PR in a running sprint is not drift"


def test_a_broken_sprint_file_fails_validation(tmp_path):
    f = tmp_path / "sprint.yaml"
    f.write_text("status: active\ndates: {start: 2026-09-01, end: not-a-date\nbacklog: [\n")
    g = tmp_path / "sprint2.yaml"
    g.write_text(yaml.safe_dump({"status": "active", "dates": {"start": "2026-09-01"}}))
    for bad in (f, g):
        r = subprocess.run([sys.executable, str(LIB / "sprint_validate.py"), str(bad)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode != 0, f"sprint_validate passed a broken sprint file {bad.name}: {r.stdout}"


@pytest.mark.production
def test_no_live_sprint_is_open_past_its_end():
    """Read-only, no fetch, no PR lookups: every space's sprints."""
    stale = []
    for space in sorted(p.name for p in ROOT.glob("[0-9]-*") if p.is_dir()):
        disc = sprint_files.discover(space, fetch=False, root=ROOT)
        for sf in disc.sprints:
            end = sprint_files._end_date(sf)
            if sf.data.get("status") != "closed" and end and end < date.today():
                stale.append(f"{space}/{sf.sprint_id}: {sf.data.get('status')} since {end}")
    assert not stale, f"{len(stale)} ended sprint(s) never closed:\n" + "\n".join(stale)
