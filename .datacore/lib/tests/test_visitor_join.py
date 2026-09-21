"""The join protocol's decisions, and the one property its alarm depends on.

`join.json` is rewritten only by a join that CONVERGED, so its age -- judged in
awake time -- is "how long has this laptop been present without converging".
That is the visitor's single alarm. Everything here protects that reading: a
dark wake is not an arrival, a failed join does not reset the clock, and the
divergence is measured before the sync that would erase it.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import visitor_join as vj  # noqa: E402
from jobs import awake  # noqa: E402

NOW = 1_800_000_000.0


def _stamp(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(vj, "RECORD", tmp_path / "join.json")
    monkeypatch.setattr(vj, "ATTEMPT", tmp_path / "join-attempt.json")
    monkeypatch.setattr(vj, "LOG", tmp_path / "join.log")
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake, "always_on", lambda machine, roster=None: False)
    monkeypatch.setattr(awake, "in_dark_wake", lambda **kw: False)


def test_a_machine_that_has_never_joined_is_due():
    assert vj.due(now=NOW, log="")[0] is True


def test_waking_since_the_last_join_is_an_arrival():
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 7200}))
    log = f"{_stamp(NOW - 600)} Wake                Wake from Deep Idle\n"
    ok, why = vj.due(now=NOW, log=log)
    assert ok and "woke" in why


def test_a_dark_wake_is_not_an_arrival(monkeypatch):
    """macOS dark-wakes every few minutes with the lid shut, usually with the
    network half up. Counting those would run the join dozens of times a night
    against a network that is not there. A person opening the lid is the
    arrival; a maintenance wake is nobody."""
    monkeypatch.setattr(awake, "in_dark_wake", lambda **kw: True)
    ok, why = vj.due(now=NOW, log="")
    assert not ok and "dark wake" in why


def test_a_darkwake_line_does_not_count_as_a_full_wake():
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 600}))
    log = f"{_stamp(NOW - 60)} DarkWake            DarkWake from Deep Idle\n"
    assert awake.last_full_wake(log=log) is None
    assert vj.due(now=NOW, log=log)[0] is False


def test_already_joined_since_waking_is_not_due():
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 300}))
    log = f"{_stamp(NOW - 900)} Wake                Wake from Deep Idle\n"
    assert vj.due(now=NOW, log=log) == (False, "joined since waking")


def test_a_flapping_lid_does_not_hammer_ten_repositories():
    vj.ATTEMPT.write_text(json.dumps({"at": NOW - 120, "ok": False}))
    ok, why = vj.due(now=NOW, log="")
    assert not ok and "ten minutes" in why


def test_a_join_that_did_not_converge_is_retried():
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 300}))
    vj.ATTEMPT.write_text(json.dumps({"at": NOW - 1200, "ok": False}))
    log = f"{_stamp(NOW - 3600)} Wake                Wake from Deep Idle\n"
    ok, why = vj.due(now=NOW, log=log)
    assert ok and "did not converge" in why


def test_a_laptop_left_open_keeps_converging():
    """No new wake for days must not mean no new join for days."""
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 6 * 3600}))
    ok, why = vj.due(now=NOW, log="")
    assert ok and "waking hours" in why


def _stub(monkeypatch, *, before, after, rc=0):
    calls = iter([before, after])
    monkeypatch.setattr(vj, "measure", lambda **kw: next(calls))
    monkeypatch.setattr(vj, "converge", lambda: (rc, "stub"))


CLEAN = {"ahead": 0, "behind": 0, "gap": 0, "blocked": [], "errors": []}


def test_divergence_is_recorded_from_before_the_sync(monkeypatch):
    """The hoarding evidence. Measured after the sync it is always zero, which
    deletes the detector while keeping its alert."""
    _stub(monkeypatch, before={**CLEAN, "ahead": 94, "behind": 12}, after=CLEAN)
    rec = vj.join(now=NOW)
    assert rec["converged"] is True
    assert (rec["ahead_by"], rec["behind_by"]) == (94, 12)
    assert json.loads(vj.RECORD.read_text())["ahead_by"] == 94


def test_a_failed_join_does_not_reset_the_alarm_clock(monkeypatch):
    """join.json's age IS the alarm. A join that learned nothing -- no network,
    a held publisher -- must not overwrite the last good record, or a laptop
    that can never converge would look freshly converged every ten minutes."""
    good = {"joined_at": NOW - 5 * 3600, "converged": True, "marker": "the last good join"}
    vj.RECORD.write_text(json.dumps(good))
    _stub(monkeypatch, before=CLEAN,
          after={**CLEAN, "gap": 20, "blocked": ["2-datacore/mac: blocked: 1 unpushed commit"]})
    rec = vj.join(now=NOW)
    assert rec["converged"] is False
    assert json.loads(vj.RECORD.read_text()) == good
    assert "blocked=2-datacore/mac" in vj.LOG.read_text()


def test_a_cycle_that_failed_is_not_convergence(monkeypatch):
    _stub(monkeypatch, before=CLEAN, after=CLEAN, rc=1)
    assert vj.join(now=NOW)["converged"] is False
    assert not vj.RECORD.exists()
