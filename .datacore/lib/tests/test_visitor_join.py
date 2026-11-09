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
    ran: list[bool] = []

    def duties(*, arrival):
        # What the record says at the moment duties run: they must see the
        # PREVIOUS record, never this join's.
        ran.append(arrival)
        return {"seen": {"rc": 0, "seconds": 0.0,
                         "last": vj.RECORD.read_text() if vj.RECORD.exists() else ""}}
    monkeypatch.setattr(vj, "run_duties", duties)
    return ran


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


# ── duties hang off the join ───────────────────────────────────────────────

def test_duties_run_after_convergence_and_before_the_record(monkeypatch):
    """Record last, with joined_at taken before the cycle: every duty artifact
    of this join is newer than the record naming it, and a verifier reading
    mid-join still sees the previous record -- so `since: join` cannot read a
    running duty as late."""
    ran = _stub(monkeypatch, before=CLEAN, after=CLEAN)
    rec = vj.join(now=NOW, log="")
    assert ran == [True]
    assert rec["duties"]["seen"]["last"] == ""          # no record existed yet
    on_disk = json.loads(vj.RECORD.read_text())
    assert on_disk["joined_at"] == NOW and "seen" in on_disk["duties"]


def test_no_duty_runs_on_a_join_that_did_not_converge(monkeypatch):
    """Not converging is the one alarm; its duties would run on a stale tree."""
    ran = _stub(monkeypatch, before=CLEAN, after=CLEAN, rc=1)
    vj.join(now=NOW, log="")
    assert ran == []


def test_a_refresh_join_is_not_an_arrival(monkeypatch):
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 5 * 3600, "arrived_at": NOW - 9 * 3600}))
    ran = _stub(monkeypatch, before=CLEAN, after=CLEAN)
    log = f"{_stamp(NOW - 10 * 3600)} Wake                Wake from Deep Idle\n"
    rec = vj.join(now=NOW, log=log)
    assert ran == [False]
    assert rec["arrival"] is False and rec["arrived_at"] == NOW - 9 * 3600


def test_waking_since_the_last_converged_join_is_an_arrival(monkeypatch):
    """Decided from the record, not from why the tick was due: the first join
    to converge after a wake begins the session, even if one failed first."""
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 5 * 3600, "arrived_at": NOW - 9 * 3600}))
    vj.ATTEMPT.write_text(json.dumps({"at": NOW - 900, "ok": False}))
    ran = _stub(monkeypatch, before=CLEAN, after=CLEAN)
    log = f"{_stamp(NOW - 3600)} Wake                Wake from Deep Idle\n"
    rec = vj.join(now=NOW, log=log)
    assert ran == [True] and rec["arrived_at"] == NOW


def test_duties_come_from_the_manifest_join_first(tmp_path):
    import yaml
    m = tmp_path / "manifest.yaml"
    m.write_text(yaml.safe_dump({"jobs": [
        {"name": "suite", "machine": "lap", "trigger": "arrival"},
        {"name": "pull", "machine": "lap", "trigger": "join"},
        {"name": "stream", "machine": "lap", "trigger": "awake"},
        {"name": "elsewhere", "machine": "srv", "trigger": "join"},
    ]}))
    assert vj.duties({"join"}, machine="lap", manifest=m) == ["pull"]
    assert vj.duties({"join", "arrival"}, machine="lap", manifest=m) == ["pull", "suite"]


# ── a failed duty is retried between joins ─────────────────────────────────

@pytest.fixture
def _retry(tmp_path, monkeypatch):
    monkeypatch.setattr(vj, "DUTIES", tmp_path / "join-duties.json")
    monkeypatch.setattr(vj, "duties", lambda triggers, **kw: [
        n for n, t in (("pull", "join"), ("suite", "arrival")) if t in triggers])
    ran: list[str] = []

    def run_duty(name):
        ran.append(name)
        return {"rc": 0, "at": NOW, "seconds": 0.0, "last": "OK"}
    monkeypatch.setattr(vj, "run_duty", run_duty)
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 3600, "converged": True}))
    return ran


def _last(**by_name):
    vj.DUTIES.write_text(json.dumps({n: {"rc": rc, "at": at} for n, (rc, at) in by_name.items()}))


def test_a_failed_join_duty_is_retried_after_fifteen_minutes(_retry):
    """One rsync warning at the 09:24 join left mac-artifact-pull red until the
    next join at 11:36. A tick between joins retries it instead."""
    _last(pull=(1, NOW - 901), suite=(0, NOW - 901))
    assert set(vj.retry_failed(now=NOW)) == {"pull"} and _retry == ["pull"]
    assert json.loads(vj.DUTIES.read_text())["pull"]["rc"] == 0


def test_a_retry_waits_its_spacing(_retry):
    _last(pull=(1, NOW - 600))
    assert vj.retry_failed(now=NOW) == {} and _retry == []


def test_the_suite_audit_is_retried_only_every_two_hours(_retry):
    """An arrival duty is the ten-to-fifteen-minute suite audit; red because a
    test fails, rerunning it every tick would only burn the battery."""
    _last(suite=(1, NOW - 3600))
    assert vj.retry_failed(now=NOW) == {}
    _last(suite=(1, NOW - 7201))
    assert set(vj.retry_failed(now=NOW)) == {"suite"}


def test_a_retry_never_rewrites_the_join_record(_retry):
    """join.json's age is the one alarm; a retry resetting it would make a
    laptop that cannot converge look freshly joined."""
    before = vj.RECORD.read_text()
    _last(pull=(1, NOW - 901))
    vj.retry_failed(now=NOW)
    assert vj.RECORD.read_text() == before


def test_no_retry_while_the_lid_is_shut(_retry, monkeypatch):
    monkeypatch.setattr(awake, "in_dark_wake", lambda **kw: True)
    _last(pull=(1, NOW - 901))
    assert vj.retry_failed(now=NOW) == {}


# ── a duty with an hour, or a period: the first presence that satisfies it ──
# (owner decision 2026-10-02: a time-of-day job becomes "on the first arrival
# after its hour"; a weekly or monthly one runs at the first presence of its
# period, never once per session -- a 4 GB backup or a paid benchmark must not
# repeat every time the lid opens).

from jobs.manifest import gate_window  # noqa: E402


def _local(y, mo, d, h, mi=0) -> float:
    return dt.datetime(y, mo, d, h, mi).timestamp()


def test_a_day_window_opens_at_its_hour():
    assert gate_window(_local(2026, 10, 2, 7, 0), "day", "08:30") is None
    assert gate_window(_local(2026, 10, 2, 9, 0), "day", "08:30") == _local(2026, 10, 2, 8, 30)
    assert gate_window(_local(2026, 10, 2, 0, 5), "day", None) == _local(2026, 10, 2, 0, 0)


def test_week_and_month_windows_open_on_their_first_day():
    # 2026-10-02 is a Friday; its week began on Monday 2026-09-28.
    assert gate_window(_local(2026, 10, 2, 9), "week", None) == _local(2026, 9, 28, 0, 0)
    assert gate_window(_local(2026, 10, 2, 9), "month", None) == _local(2026, 10, 1, 0, 0)
    assert gate_window(_local(2026, 10, 1, 5), "month", "12:00") is None


@pytest.fixture
def _gated(tmp_path, monkeypatch):
    """pull: join duty; suite: plain arrival duty; journal: arrival, once a day
    after 08:30."""
    monkeypatch.setattr(vj, "DUTIES", tmp_path / "join-duties.json")
    monkeypatch.setattr(vj, "duties", lambda triggers, **kw: [
        n for n, t in (("pull", "join"), ("suite", "arrival"), ("journal", "arrival"))
        if t in triggers])
    monkeypatch.setattr(vj, "gates", lambda **kw: {"journal": ("day", "08:30")})
    ran: list[str] = []

    def run_duty(name, now=None):
        ran.append(name)
        return {"rc": 0, "at": now, "seconds": 0.0, "last": "OK"}
    monkeypatch.setattr(vj, "run_duty", lambda name: run_duty(name, vj._clock()))
    return ran


def test_an_arrival_before_the_hour_leaves_the_timed_duty_waiting(_gated, monkeypatch):
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 7, 0))
    vj.run_duties(arrival=True)
    assert _gated == ["pull", "suite"]


def test_the_first_arrival_after_the_hour_runs_it_once_a_day(_gated, monkeypatch):
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 9, 0))
    vj.run_duties(arrival=True)
    assert _gated == ["pull", "suite", "journal"]
    _gated.clear()
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 14, 0))
    vj.run_duties(arrival=True)           # a second session the same day
    assert _gated == ["pull", "suite"]


def test_a_refresh_join_after_the_hour_runs_a_timed_duty_still_owed(_gated, monkeypatch):
    """Arrived at 07:00, still here at 11:00: the journal is owed today."""
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 11, 0))
    vj.run_duties(arrival=False)
    assert _gated == ["pull", "journal"]


def test_a_tick_runs_a_timed_duty_once_its_hour_comes(_gated, monkeypatch):
    """Arrived at 07:00 and present at 08:35: the next tick runs it, not the
    next join four waking hours later."""
    vj.RECORD.write_text(json.dumps({"joined_at": _local(2026, 10, 2, 7, 0), "converged": True}))
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 8, 35))
    assert set(vj.run_due_gated()) == {"journal"} and _gated == ["journal"]
    _gated.clear()
    assert vj.run_due_gated() == {} and _gated == []      # ran today already


def test_a_tick_runs_no_timed_duty_before_its_hour_or_with_the_lid_shut(_gated, monkeypatch):
    vj.RECORD.write_text(json.dumps({"joined_at": _local(2026, 10, 2, 7, 0), "converged": True}))
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 8, 0))
    assert vj.run_due_gated() == {}
    monkeypatch.setattr(vj, "_clock", lambda: _local(2026, 10, 2, 9, 0))
    monkeypatch.setattr(awake, "in_dark_wake", lambda **kw: True)
    assert vj.run_due_gated() == {} and _gated == []


def test_a_failed_timed_duty_is_not_retried_before_its_next_window(_gated, monkeypatch):
    """Failed yesterday at 09:00; at 07:00 today its window has not opened."""
    vj.RECORD.write_text(json.dumps({"joined_at": _local(2026, 10, 2, 6, 0), "converged": True}))
    vj.DUTIES.write_text(json.dumps({"journal": {"rc": 1, "at": _local(2026, 10, 1, 9, 0)}}))
    assert vj.retry_failed(now=_local(2026, 10, 2, 7, 0)) == {}


# ── the verifier judges a timed duty by its window, not by the arrival ─────

def _since(tmp_path, monkeypatch, *, joined_at, mtime, now, every="day", after="08:30"):
    import os
    from jobs.checks import run_check
    from jobs.manifest import Artifact
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path))
    (tmp_path / "join.json").write_text(json.dumps({"joined_at": joined_at, "arrived_at": joined_at}))
    out = tmp_path / "duty.log"
    out.write_text("ok\n")
    os.utime(out, (mtime, mtime))
    return run_check(Artifact(path=str(out), check="exists", since="arrival",
                              every=every, after=after), now=now)


def test_a_timed_duty_waiting_for_its_hour_is_not_red(tmp_path, monkeypatch):
    """Arrived 07:00, verified 07:45: the journal is not owed yet. A red here
    would be delegated for repair every morning."""
    assert _since(tmp_path, monkeypatch, joined_at=_local(2026, 10, 2, 7, 0),
                  mtime=_local(2026, 10, 1, 9, 0), now=_local(2026, 10, 2, 7, 45)) == []


def test_a_timed_duty_owed_since_a_join_after_its_hour_is_red(tmp_path, monkeypatch):
    errs = _since(tmp_path, monkeypatch, joined_at=_local(2026, 10, 2, 9, 0),
                  mtime=_local(2026, 10, 1, 9, 0), now=_local(2026, 10, 2, 10, 0))
    assert errs and "has not run" in errs[0]


def test_a_timed_duty_that_ran_in_its_window_is_green(tmp_path, monkeypatch):
    assert _since(tmp_path, monkeypatch, joined_at=_local(2026, 10, 2, 11, 0),
                  mtime=_local(2026, 10, 2, 8, 35), now=_local(2026, 10, 2, 12, 0)) == []


def test_a_monthly_duty_is_owed_once_a_month_not_each_session(tmp_path, monkeypatch):
    assert _since(tmp_path, monkeypatch, joined_at=_local(2026, 10, 20, 9, 0),
                  mtime=_local(2026, 10, 1, 12, 5), now=_local(2026, 10, 20, 10, 0),
                  every="month", after="12:00") == []


def test_the_manifest_refuses_a_malformed_gate():
    from jobs.manifest import validate_manifest
    base = {"name": "j", "machine": "mac", "schedule": "on arrival", "cmd": "true",
            "trigger": "arrival", "artifacts": [{"path": "~/x", "check": "exists", "since": "arrival"}]}
    for bad in ({"every": "fortnight"}, {"after": "8.30"}, {"after": "25:00"},
                {"every": "day", "trigger": "join"}):
        with pytest.raises(ValueError):
            validate_manifest({"version": 1, "jobs": [{**base, **bad}]})
    validate_manifest({"version": 1, "jobs": [{**base, "every": "week", "after": "03:00"}]})
