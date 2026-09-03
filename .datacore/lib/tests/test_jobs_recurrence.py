"""Recurrence escalation — DIP-0035 Open Question #2, using DIP-0031's rule.

DIP-0035 shipped detection and explicitly deferred remediation: "whether a
Phase 6+ follow-on should add auto-retry or auto-escalation on repeated failure
is unresolved." DIP-0031 had already settled the threshold for nightshift task
failures — ">=3 consecutive runs is a recurring failure" — so this adopts that
number rather than inventing a second one for the same idea.

Measured cause: box-projection-drift had failed 22 consecutive times on winston,
correctly, while the drift it reported grew from 49 to 339 extra projected
tasks. A 22nd failure rendered identically to a 1st.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest


@pytest.fixture
def rec(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path))
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from jobs import recurrence as R
    importlib.reload(R)
    R.STATE = tmp_path / "job-verify-recurrence.json"
    return R


def test_threshold_matches_dip_0031(rec):
    """One number for one idea. Two thresholds would be bug class 1."""
    assert rec.RECURRING_AFTER == 3


def test_below_threshold_the_message_is_unchanged(rec):
    for i in range(1, 3):
        r = rec.record("j", failed=True, today=f"2026-09-0{i}")
        assert not r["recurring"]
        assert rec.describe("j", r, 1).startswith("job.verify FAILED:")


def test_at_threshold_the_message_changes_subject(rec):
    for i in range(1, 4):
        r = rec.record("j", failed=True, today=f"2026-09-0{i}")
    assert r["recurring"] and r["consecutive"] == 3
    msg = rec.describe("j", r, 1)
    assert "RECURRING" in msg
    assert "3 consecutive runs" in msg
    assert "2026-09-01" in msg, "must name when it started, or a repeat is invisible"
    assert "decision" in msg


def test_a_pass_resets_the_count(rec):
    for i in range(1, 5):
        rec.record("j", failed=True, today=f"2026-09-0{i}")
    r = rec.record("j", failed=False, today="2026-09-05")
    assert r["consecutive"] == 0 and not r["recurring"]


def test_recovery_does_not_carry_over_into_a_later_failure(rec):
    """An intermittent job must not accumulate toward escalation across
    unrelated incidents — that would be a false alarm of the kind this exists
    to stop."""
    for i in range(1, 5):
        rec.record("j", failed=True, today=f"2026-09-0{i}")
    rec.record("j", failed=False, today="2026-09-05")
    r = rec.record("j", failed=True, today="2026-09-06")
    assert r["consecutive"] == 1 and not r["recurring"]


def test_corrupt_state_degrades_to_first_occurrence_not_an_exception(rec):
    """Bookkeeping must never take down the check that reports everything else.
    Losing the count degrades to today's behaviour, which is acceptable."""
    rec.STATE.parent.mkdir(parents=True, exist_ok=True)
    rec.STATE.write_text("{ this is not json")
    r = rec.record("j", failed=True, today="2026-09-01")
    assert r["consecutive"] == 1


def test_summary_lists_only_recurring_jobs_worst_first(rec):
    for i in range(1, 6):
        rec.record("bad", failed=True, today=f"2026-09-0{i}")
    for i in range(1, 4):
        rec.record("worse", failed=True, today=f"2026-09-0{i}")
    rec.record("fine", failed=False, today="2026-09-01")
    s = rec.summary()
    assert [r["job"] for r in s] == ["bad", "worse"]
    assert "fine" not in [r["job"] for r in s]


def test_concurrent_records_do_not_lose_updates(rec):
    """Two verifiers at once -- cron and a hand run -- must not race. Without
    the lock both read N and both write N+1; worse, a pass's reset could be
    overwritten by a concurrent fail's increment and a recovered job would stay
    'recurring'. 40 threads x 1 increment must equal exactly 40."""
    import threading
    errors = []

    def go():
        try:
            rec.record("j", failed=True, today="2026-09-03")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=go) for _ in range(40)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert not errors
    assert rec._load()["j"]["consecutive"] == 40
