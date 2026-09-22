"""A visitor carries only what is about itself.

The roster has said "A WORKSTATION IS NOT A DEGRADED SERVER" since before this
test existed, and nothing enforced it. The laptop accumulated twenty job
contracts -- most of them network duties with clock deadlines, seven added in
three days by sessions that were fixing laptop alerts -- and spent a month
alerting about its own lid. Each alert got its own patch: awake-time accounting,
a dark-wake hold, launchd for cron, a grace period measured on the right clock.
One missing rule, applied piecemeal.

This is the rule. A node whose presence is not promised (jobs.awake.node_class
== "visitor") may carry a job only if the job says why it belongs there:

    scope: local    it is about this machine: its working copy, its own feed
    scope: pinned   a network duty stuck here by a stated constraint, with the
                    condition that frees it (`unpin_when`). Pinned is debt, and
                    the point of naming it is that it can be counted.

Anything else belongs on a resident, where a deadline is an honest thing to
have. Adding a teammate's laptop is then one roster entry and no new alert
surface, which is the property that has to hold as the number of humans grows.

A VISITOR CARRIES NO CLOCK (2026-09-22). Scoping said WHY a job was on the
laptop; it still ran at 03:30 or 08:50 like a server's. A visitor's duties are
session-scoped -- they happen because a person opened the lid -- so each names
the session event that fires it (`trigger`, jobs.manifest.TRIGGERS), never an
hour, and each artifact is bounded in session time: `since: join|arrival`
(written for this session, judged against join.json) or `max_age_hours`, which
on a visitor is measured in awake time.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from jobs import awake  # noqa: E402
from jobs.manifest import TRIGGERS, Artifact  # noqa: E402

MANIFEST = LIB / "jobs" / "manifest.yaml"


def _roster(tmp_path, **servers) -> Path:
    p = tmp_path / "infrastructure.yaml"
    p.write_text(yaml.safe_dump({"servers": servers}))
    return p


def _visitor_jobs() -> list[dict]:
    jobs = yaml.safe_load(MANIFEST.read_text())["jobs"]
    return [j for j in jobs if awake.node_class(j.get("machine", "")) == "visitor"]


def test_a_workstation_is_a_visitor_and_a_server_is_a_resident(tmp_path):
    roster = _roster(tmp_path, lap={"kind": "workstation"}, srv={"kind": "server"},
                     pinned_up={"kind": "workstation", "always_on": True})
    assert awake.node_class("lap", roster) == "visitor"
    assert awake.node_class("srv", roster) == "resident"
    # An explicit promise outranks the kind: a desktop that never sleeps is a
    # resident whatever it is called.
    assert awake.node_class("pinned_up", roster) == "resident"


def test_an_undeclared_machine_is_a_resident(tmp_path):
    """A host becomes a visitor only by being declared one, so adding this type
    changed no existing contract's behaviour."""
    assert awake.node_class("never-heard-of-it", _roster(tmp_path)) == "resident"


def test_this_installation_actually_has_a_visitor():
    """Guards the guard: if the roster stops classing the laptop as a visitor,
    every test below passes vacuously over an empty list."""
    assert _visitor_jobs(), "no job runs on a visitor -- is the roster's kind: workstation gone?"


def test_every_job_on_a_visitor_says_why_it_is_there():
    bad = [j["name"] for j in _visitor_jobs() if j.get("scope") not in ("local", "pinned")]
    assert not bad, (
        "these jobs run on a machine whose presence is not promised and do not say why:\n  "
        + "\n  ".join(bad)
        + "\nA network duty with a deadline belongs on a resident (nightshift, box). If the "
          "job is genuinely about this machine, add `scope: local` and `why_local:`. If a "
          "constraint forces it here, add `scope: pinned`, `why_pinned:` and `unpin_when:`."
    )


def test_a_local_job_states_its_reason():
    for j in _visitor_jobs():
        if j.get("scope") == "local":
            assert str(j.get("why_local") or "").strip(), f"{j['name']}: scope: local with no why_local"


def test_a_pinned_job_names_the_condition_that_frees_it():
    """Pinned is debt. Debt with no repayment condition is just a permanent
    exception wearing a label."""
    for j in _visitor_jobs():
        if j.get("scope") == "pinned":
            assert str(j.get("why_pinned") or "").strip(), f"{j['name']}: pinned with no reason"
            assert str(j.get("unpin_when") or "").strip(), f"{j['name']}: pinned with no way out"


#: Three on 2026-09-21 (config-drift, registry-gc, suite-audit); two on
#: 2026-09-22, when registry-gc became a test. Only ever lowered.
PINNED_CAP = 2


def test_pinned_debt_does_not_grow_unnoticed():
    """Raising this number is a decision someone should be seen making."""
    pinned = sorted(j["name"] for j in _visitor_jobs() if j.get("scope") == "pinned")
    assert len(pinned) <= PINNED_CAP, f"pinned duties on a visitor grew to {len(pinned)}: {pinned}"


# ── session-scoped, not clock-scoped ───────────────────────────────────────

#: What a clock looks like in a `schedule:` line: a five-field cron entry, the
#: word cron, a launchd calendar, a time of day, or a named period.
_CLOCK = re.compile(
    r"^\s*[\d*/,-]+\s+[\d*/,-]+\s+[\d*/,-]+\s+[\d*/,-]+\s+[\d*/,-]+(?:\s|$)"
    r"|\bcron\b|StartCalendarInterval|\b\d{1,2}:\d{2}\b"
    r"|\b(daily|hourly|nightly|weekly)\b",
    re.IGNORECASE)


def test_every_visitor_job_names_the_session_event_that_fires_it():
    bad = [f"{j['name']}: trigger={j.get('trigger')!r}" for j in _visitor_jobs()
           if j.get("trigger") not in TRIGGERS]
    assert not bad, ("a visitor's duty happens because a person arrived; say which "
                     f"event fires it ({', '.join(sorted(TRIGGERS))}):\n  " + "\n  ".join(bad))


def test_no_visitor_job_is_scheduled_by_a_clock():
    bad = [f"{j['name']}: {j.get('schedule')!r}" for j in _visitor_jobs()
           if _CLOCK.search(str(j.get("schedule", "")))]
    assert not bad, ("a visitor may not carry a cron line or a fixed launchd hour -- hang it "
                     "off the join (trigger: join|arrival) or move it to a resident:\n  "
                     + "\n  ".join(bad))


def test_every_visitor_artifact_is_bounded_in_session_time():
    """`since` proves the duty ran for this session; `max_age_hours` on a
    visitor is awake time. An artifact with neither can be any age at all."""
    for j in _visitor_jobs():
        for a in j["artifacts"]:
            assert a.get("since") or a.get("max_age_hours") is not None, \
                f"{j['name']}: {a['path']} has no bound"


def test_a_duty_fired_by_the_join_proves_it_ran_this_session():
    """A duty on `join`/`arrival` runs when the join says so, not on a cadence,
    so an age bound alone cannot tell "ran and was fine" from "the join stopped
    calling it". At least one artifact must be judged against the join record.
    And an arrival duty is not re-run on every join, so it cannot be held to
    `since: join`."""
    for j in _visitor_jobs():
        trig = j.get("trigger")
        sinces = [a.get("since") for a in j["artifacts"] if a.get("since")]
        if trig in ("join", "arrival"):
            assert sinces, f"{j['name']}: trigger {trig} with no `since:` artifact"
        if trig == "arrival":
            assert "join" not in sinces, f"{j['name']}: runs once a session, judged per join"
        if trig in ("wake", "awake"):
            assert not sinces, f"{j['name']}: trigger {trig} is not fired by a join; bound it by age"


def test_the_join_runs_every_duty_that_names_it():
    """A trigger nothing acts on is a schedule with no scheduler -- the state
    mac-artifact-pull was in for a month in 2026. Every join/arrival duty must be
    what visitor_join actually picks up for its machine."""
    import visitor_join
    for machine in {j["machine"] for j in _visitor_jobs()}:
        want = {j["name"] for j in _visitor_jobs()
                if j["machine"] == machine and j.get("trigger") in ("join", "arrival")}
        got = set(visitor_join.duties({"join", "arrival"}, machine=machine, manifest=MANIFEST))
        assert got == want


def _join_record(tmp_path, monkeypatch, *, joined_at, arrived_at):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path))
    (tmp_path / "join.json").write_text(json.dumps({"joined_at": joined_at, "arrived_at": arrived_at}))


def test_since_join_passes_a_duty_that_ran_after_the_join(tmp_path, monkeypatch):
    from jobs.checks import run_check
    now = time.time()
    _join_record(tmp_path, monkeypatch, joined_at=now - 600, arrived_at=now - 7200)
    out = tmp_path / "duty.log"
    out.write_text("0 problem(s)\n")
    os.utime(out, (now - 60, now - 60))
    assert run_check(Artifact(path=str(out), check="exists", since="join"), now=now) == []


def test_since_join_fails_a_duty_that_has_not_run_since_the_join(tmp_path, monkeypatch):
    """The failure the old clock bounds could not express: fresh by age, and
    still not run for this session."""
    from jobs.checks import run_check
    now = time.time()
    _join_record(tmp_path, monkeypatch, joined_at=now - 600, arrived_at=now - 7200)
    out = tmp_path / "duty.log"
    out.write_text("0 problem(s)\n")
    os.utime(out, (now - 900, now - 900))
    errs = run_check(Artifact(path=str(out), check="exists", since="join"), now=now)
    assert errs and "has not run" in errs[0]
    # ...and the same artifact satisfies `since: arrival`, which is older.
    assert run_check(Artifact(path=str(out), check="exists", since="arrival"), now=now) == []


def test_since_with_no_join_on_record_is_unprovable_not_green(tmp_path, monkeypatch):
    from jobs.checks import run_check
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path))
    out = tmp_path / "duty.log"
    out.write_text("x\n")
    errs = run_check(Artifact(path=str(out), check="exists", since="join"))
    assert errs and "unprovable" in errs[0]


def _refused(tmp_path, monkeypatch, job, test):
    fake = tmp_path / "manifest.yaml"
    fake.write_text(yaml.safe_dump({"jobs": [job]}))
    monkeypatch.setattr(sys.modules[__name__], "MANIFEST", fake)
    monkeypatch.setattr(awake, "always_on", lambda m, r=None: m != "lap")
    with pytest.raises(AssertionError, match=job["name"]):
        test()


@pytest.mark.parametrize("schedule", ["30 3 * * *", "cron 25 * * * * on mac",
                                      "launchd io.x @ 08:50, 14:50", "StartCalendarInterval Hour=7",
                                      "daily after the briefing"])
def test_the_rule_refuses_a_clock_on_a_visitor(tmp_path, monkeypatch, schedule):
    job = {"name": "lap-clocked", "machine": "lap", "scope": "local", "why_local": "x",
           "trigger": "join", "schedule": schedule, "cmd": "true",
           "artifacts": [{"path": "~/x", "check": "exists", "since": "join"}]}
    _refused(tmp_path, monkeypatch, job, test_no_visitor_job_is_scheduled_by_a_clock)


def test_the_rule_refuses_a_visitor_job_with_no_trigger(tmp_path, monkeypatch):
    job = {"name": "lap-untriggered", "machine": "lap", "scope": "local", "why_local": "x",
           "schedule": "on join", "cmd": "true",
           "artifacts": [{"path": "~/x", "check": "exists", "since": "join"}]}
    _refused(tmp_path, monkeypatch, job, test_every_visitor_job_names_the_session_event_that_fires_it)


def test_the_rule_refuses_a_join_duty_judged_only_by_age(tmp_path, monkeypatch):
    job = {"name": "lap-aged", "machine": "lap", "scope": "local", "why_local": "x",
           "trigger": "join", "schedule": "on join", "cmd": "true",
           "artifacts": [{"path": "~/x", "check": "exists", "max_age_hours": 26}]}
    _refused(tmp_path, monkeypatch, job, test_a_duty_fired_by_the_join_proves_it_ran_this_session)


def test_the_rule_refuses_a_network_duty_on_a_visitor(tmp_path, monkeypatch):
    """Prove the guard bites, rather than trusting that it would."""
    doc = {"jobs": [{"name": "lap-fleet-poll", "machine": "lap", "schedule": "0 * * * *",
                     "cmd": "true", "artifacts": [{"path": "~/x", "check": "exists"}]}]}
    fake = tmp_path / "manifest.yaml"
    fake.write_text(yaml.safe_dump(doc))
    roster = _roster(tmp_path, lap={"kind": "workstation"})
    monkeypatch.setattr(sys.modules[__name__], "MANIFEST", fake)
    monkeypatch.setattr(awake, "always_on", lambda m, r=None: m != "lap")
    with pytest.raises(AssertionError, match="lap-fleet-poll"):
        test_every_job_on_a_visitor_says_why_it_is_there()
