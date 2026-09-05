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



def test_prune_forgets_jobs_the_manifest_no_longer_names(rec):
    """A record for a deleted or renamed job can never receive a pass, so it
    would stay recurring forever: box-registry-gc, 2026-09-03."""
    for _ in range(3):
        rec.record("box-registry-gc", failed=True)
    rec.record("mac-registry-gc", failed=True)
    assert [r["job"] for r in rec.summary()] == ["box-registry-gc"]
    gone = rec.prune({"mac-registry-gc", "other"})
    assert gone == ["box-registry-gc"]
    assert rec.summary() == []
    assert "mac-registry-gc" in rec._load(), "a known job's record must survive the prune"
    assert rec.prune({"mac-registry-gc"}) == [], "idempotent"


def test_record_survives_a_lock_failure(rec, monkeypatch, capsys):
    """A read-only or NFS home must degrade to an unserialised update, never
    abort the verification run that called record()."""
    def _boom(*a, **k):
        raise OSError("flock not supported")
    monkeypatch.setattr(rec.fcntl, "flock", _boom)
    r = rec.record("j", failed=True)
    assert r["consecutive"] == 1
    assert "lock unavailable" in capsys.readouterr().err
    assert rec._load()["j"]["consecutive"] == 1, "the update must still be persisted"


def test_prune_with_no_known_jobs_is_a_no_op(rec):
    rec.record("j", failed=True)
    assert rec.prune(set()) == []
    assert "j" in rec._load(), "an empty manifest must not wipe every counter"



def test_recurring_failures_alert_at_escalation_then_once_a_day(rec):
    """Below the threshold every failure alerts; at it, once; after it, once a day."""
    decisions = []
    for n in range(1, 7):
        r = rec.record("j", failed=True, today="2026-09-03")
        a = rec.should_alert(r, today="2026-09-03")
        decisions.append(a)
        if a:
            rec.note_alerted("j", today="2026-09-03")
    assert decisions == [True, True, True, False, False, False]
    r = rec.record("j", failed=True, today="2026-09-04")
    assert rec.should_alert(r, today="2026-09-04") is True, "a new day gets one reminder"
    rec.note_alerted("j", today="2026-09-04")
    r = rec.record("j", failed=True, today="2026-09-04")
    assert rec.should_alert(r, today="2026-09-04") is False
    rec.record("j", failed=False, today="2026-09-04")
    r = rec.record("j", failed=True, today="2026-09-04")
    assert rec.should_alert(r, today="2026-09-04") is True, "a pass resets: the next failure is a first failure again"


def test_the_same_failed_artifact_is_counted_once(rec):
    """2026-09-05: the verifier ran every 30 min, the producer three times a day;
    the streak was a clock. A signature that has not changed is the same failure."""
    r1 = rec.record("j", failed=True, today="2026-09-05", artifact_sig="log@100")
    r2 = rec.record("j", failed=True, today="2026-09-05", artifact_sig="log@100")
    r3 = rec.record("j", failed=True, today="2026-09-05", artifact_sig="log@200")
    assert (r1["consecutive"], r1["same_artifact"]) == (1, False)
    assert (r2["consecutive"], r2["same_artifact"]) == (1, True), "unchanged artifact: not counted again"
    assert (r3["consecutive"], r3["same_artifact"]) == (2, False), "a regenerated artifact that still fails counts"


def test_a_pass_keeps_the_task_id_for_the_verifier_to_close(rec):
    for i in range(3):
        rec.record("j", failed=True, today="2026-09-05", artifact_sig=f"log@{i}")
    rec.note_task("j", "org-task-1")
    r = rec.record("j", failed=False, today="2026-09-06")
    assert r["consecutive"] == 0 and r["task_id"] == "org-task-1"
    rec.note_task("j", None)
    assert "task_id" not in rec._load()["j"]
