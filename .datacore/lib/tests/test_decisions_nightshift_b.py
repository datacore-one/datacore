"""Owner decisions N5, N7 and N8 (2026-09-23, DatacoreSpec/NightshiftGates.lean).

N5  A failed local commit of the canary input is `failed`, not `blocked`; a
    `blocked` verdict older than 48 h fails the check.
N7  When recurrence cannot take its lock or save, verification continues but a
    WARNING names the lost reset, on stderr and in the job's record/report.
N8  ROADMAP is required by one shared function; the gate and the executor both
    call it (the executor half is tested in modules/nightshift/tests).
"""
import importlib
import json
import pathlib
import subprocess
import sys
import time

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import delegation_canary as canary  # noqa: E402
import delegation_requirements as dr  # noqa: E402
import ai_task_gate  # noqa: E402


# ------------------------------------------------------------------ N5

@pytest.fixture
def canary_state(tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "RESULT", tmp_path / "canary.json")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", tmp_path / "absent.yaml")
    actor_identity._PRINCIPALS_CACHE.clear()
    return tmp_path


def _args(space, age=26.0):
    ns = type("A", (), {})()
    ns.space, ns.assignee, ns.max_age_hours = space, "miles", age
    return ns


def test_a_failed_local_commit_is_failed_not_blocked(canary_state):
    """Canary.blocked_forever: a space whose commit fails wrote `blocked`,
    exited 0 and passed the contract every day."""
    space = canary_state / "1-not-a-repo"
    space.mkdir()
    assert canary.cmd_run(_args(space)) == 1
    assert json.loads(canary.RESULT.read_text())["verdict"] == "failed"


def test_a_blocked_verdict_older_than_48h_fails_the_check(canary_state):
    space = canary_state / "1-space"
    space.mkdir()
    canary.RESULT.write_text(json.dumps({"verdict": "blocked", "at": time.time() - 49 * 3600,
                                         "detail": "offline"}))
    assert canary.cmd_check(_args(space)) == 1
    assert json.loads(canary.RESULT.read_text())["verdict"] == "failed"


def test_a_recent_blocked_verdict_still_passes(canary_state):
    space = canary_state / "1-space"
    space.mkdir()
    canary.RESULT.write_text(json.dumps({"verdict": "blocked", "at": time.time() - 47 * 3600,
                                         "detail": "offline"}))
    assert canary.cmd_check(_args(space)) == 0


# ------------------------------------------------------------------ N7

@pytest.fixture
def rec(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path))
    from jobs import recurrence as R
    importlib.reload(R)
    R.STATE = tmp_path / "job-verify-recurrence.json"
    return R


def test_a_failed_save_of_a_reset_warns_loudly(rec, monkeypatch, capsys):
    for i in range(1, 4):
        rec.record("j", failed=True, today=f"2026-09-0{i}")
    real_replace = pathlib.Path.replace

    def refuse(self, target):
        raise OSError("read-only")
    monkeypatch.setattr(pathlib.Path, "replace", refuse)
    r = rec.record("j", failed=False, today="2026-09-04")
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)

    err = capsys.readouterr().err
    assert "WARNING" in err and "j" in err and "reset" in err, err
    assert "WARNING" in r.get("warning", ""), "the job's record must carry the warning"
    assert "3" in r["warning"], "must name the streak that was not reset"


def test_a_lost_lock_warns_loudly_and_verification_continues(rec, monkeypatch, capsys):
    rec.record("j", failed=True, today="2026-09-01")

    def no_lock():
        raise OSError("no flock here")
    monkeypatch.setattr(rec, "_locked", no_lock)
    r = rec.record("j", failed=False, today="2026-09-02")
    err = capsys.readouterr().err
    assert r["consecutive"] == 0
    assert "WARNING" in err and "j" in err, err
    assert "WARNING" in r.get("warning", "")


def test_a_failure_whose_save_failed_says_so_in_the_alert(rec, monkeypatch):
    monkeypatch.setattr(rec, "_save", lambda d: False)
    r = rec.record("j", failed=True, today="2026-09-01")
    assert "WARNING" in rec.describe("j", r, 1)


def test_a_clean_record_carries_no_warning(rec, capsys):
    r = rec.record("j", failed=False, today="2026-09-01")
    assert "warning" not in r
    assert "WARNING" not in capsys.readouterr().err


# ------------------------------------------------------------------ N8

def test_the_shared_function_owns_the_roadmap_clause():
    props = {"SURFACE": "datacore", "DONE_WHEN": "tests pass"}
    assert dr.execution_gaps(props) == []
    assert dr.execution_gaps(props, roadmap_required=True) == ["ROADMAP"]
    assert dr.execution_gaps({**props, "ROADMAP": "o1"}, roadmap_required=True) == []


def test_roadmap_spaces_are_the_spaces_with_a_roadmap_file(tmp_path):
    (tmp_path / "2-a").mkdir()
    (tmp_path / "2-a" / "roadmap.yaml").write_text("x: 1\n")
    (tmp_path / "0-b").mkdir()
    assert dr.roadmap_spaces(tmp_path) == {"2-a"}


def test_the_gate_uses_the_shared_roadmap_clause():
    props = {"SURFACE": "datacore", "DONE_WHEN": "tests pass"}
    assert ai_task_gate._missing(props, "2-a", roadmap_spaces={"2-a"}) == [
        "no ROADMAP — the agent cannot tell which outcome this serves"]
    assert ai_task_gate._missing(props, "0-b", roadmap_spaces={"2-a"}) == []


def test_a_lost_reset_warning_reaches_the_verifier_report(monkeypatch, capsys):
    import job_verify
    from jobs import recurrence
    monkeypatch.setattr(job_verify, "_NO_EMIT", False)
    monkeypatch.setattr(recurrence, "record",
                        lambda name, failed, **kw: {"warning": "reset of 3 not saved"})
    job_verify._note_pass("fixture-job")
    assert "WARNING fixture-job: reset of 3 not saved" in capsys.readouterr().out
