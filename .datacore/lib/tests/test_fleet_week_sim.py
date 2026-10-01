"""The fleet week simulator's own tests (issue #222).

Two layers. The schedule reader is pure and runs anywhere. The fleet itself
needs Linux with libfaketime and a prepared seed (`fleet_week_sim.py prepare`),
so those tests run inside the simulator's container:

    fleet_week_sim.py docker --selftest

and are skipped, with the reason, everywhere else.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import fleet_week_sim as sim  # noqa: E402

UTC = dt.timezone.utc
THU = dt.date(2026, 10, 1)
SUN = dt.date(2026, 10, 4)


def at(day: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC)


# -- the schedule reader ------------------------------------------------------

def test_plain_cron_fires_at_its_minutes():
    spec = sim.parse_schedule("30 2 * * *")
    assert spec.kind == "cron"
    assert sim.fires(spec, THU) == [at(THU, 2, 30)]


def test_cron_inside_prose_is_found():
    spec = sim.parse_schedule("cron 25 * * * * on the box")
    assert spec.kind == "cron"
    assert len(sim.fires(spec, THU)) == 24


def test_sub_hourly_cron_is_compressed_to_the_minimum_interval():
    spec = sim.parse_schedule("*/15 * * * *")
    got = sim.fires(spec, THU, min_interval_s=3600)
    assert len(got) == 24 and got[0] == at(THU, 0, 0)
    assert len(sim.fires(spec, THU, min_interval_s=900)) == 96


def test_weekly_cron_fires_only_on_its_weekday():
    spec = sim.parse_schedule("15 6 * * 0")
    assert sim.fires(spec, THU) == []
    assert sim.fires(spec, SUN) == [at(SUN, 6, 15)]


def test_clock_times_in_prose():
    spec = sim.parse_schedule("systemd datacore-fleet-sync.timer, 06:10 and 18:10 UTC on the box")
    assert sim.fires(spec, THU) == [at(THU, 6, 10), at(THU, 18, 10)]
    assert sim.fires(sim.parse_schedule("launchd com.datacore.bench: Sunday 03:00"), THU) == []
    assert sim.fires(sim.parse_schedule("launchd io.datacore.x: weekdays 08:25"), SUN) == []


def test_intervals_and_daemons():
    # A continuous service that ticks internally is a daemon: run as a job it
    # sleeps out its own interval and blocks the simulated hour.
    assert sim.parse_schedule("continuous (venture-heartbeat.service, one tick every 1800 s)").kind == "daemon"
    tick = sim.parse_schedule("launchd io.datacore.state-sync: every 300 s")
    assert tick.kind == "interval" and len(sim.fires(tick, THU, min_interval_s=3600)) == 24
    assert sim.parse_schedule("tris-heartbeat.timer (every 30 min)").kind == "interval"
    assert sim.parse_schedule("continuous (launchd KeepAlive daemon, RunAtLoad)").kind == "daemon"
    assert sim.parse_schedule("continuous (datacore-agent-stream-tail sidecar)").kind == "daemon"


def test_a_visitor_runs_only_while_awake_and_catches_up_at_wake():
    join = sim.parse_schedule("on join: visitor_join.py runs it", trigger="join")
    assert sim.fires(join, THU, visitor=True) == [at(THU, h, 1) for h in (8, 12, 16, 20)]
    midnight = sim.parse_schedule("0 0 * * *")
    # Asleep at midnight: launchd runs the missed calendar job once on wake.
    assert sim.fires(midnight, THU, visitor=True) == [at(THU, 8)]


# -- the fleet (Linux + libfaketime + a seed) ---------------------------------

SEED = Path(os.environ.get("FLEET_SIM_SEED", "/seed"))
needs_fleet = pytest.mark.skipif(
    not (sim.libfaketime() and (SEED / "core-src").is_dir()),
    reason="needs Linux with libfaketime and a prepared seed: run `fleet_week_sim.py docker --selftest`")

ROSTER = {"servers": {
    "alpha": {"kind": "server", "ssh_alias": "alpha", "ledger_actors": ["alpha"],
              "access": {"actor": "alpha", "hostname": "alpha"}},
    "beta": {"kind": "server", "ssh_alias": "beta", "ledger_actors": ["beta"],
             "access": {"actor": "beta", "hostname": "beta"}}},
    "roles": {"always_on": "alpha", "executor": "beta"}}

JOBS = {"version": 1, "jobs": [
    {"name": "alpha-heartbeat", "machine": "alpha", "schedule": "0 * * * *",
     "cmd": "mkdir -p ~/.datacore/cos && touch ~/.datacore/cos/heartbeat",
     "artifacts": [{"path": "~/.datacore/cos/heartbeat", "check": "exists", "max_age_hours": 2}]},
    {"name": "alpha-report", "machine": "alpha", "schedule": "0 4 * * *",
     "cmd": "mkdir -p ~/.datacore/state && claude -p 'write the report' "
            "&& echo \"report ok $(date +%F)\" >> ~/.datacore/state/report.log",
     "artifacts": [{"path": "~/.datacore/state/report.log", "check": "last_line_regex",
                    "arg": "report ok", "max_age_hours": 26}]},
    {"name": "beta-heartbeat", "machine": "beta", "schedule": "30 * * * *",
     "cmd": "mkdir -p ~/.datacore/cos && touch ~/.datacore/cos/heartbeat",
     "artifacts": [{"path": "~/.datacore/cos/heartbeat", "check": "exists", "max_age_hours": 2}]},
]}


def _tiny(tmp_path: Path, faults: list) -> dict:
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(ROSTER))
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps(JOBS))
    out = tmp_path / "out"
    return sim.run_week(sim.Options(
        seed=SEED, out=out, days=1, start=THU, faults=faults, roster=roster, manifest=manifest,
        spaces=["personal"], evals="off", workdir=tmp_path / "fleet"))


@needs_fleet
def test_the_simulated_clock_moves_and_sleep_still_works(tmp_path):
    """Under the fake clock a job sees the simulated date, and sleeping and
    subprocess timeouts still work (they raised EINVAL in the first week run)."""
    opts = sim.Options(seed=SEED, out=tmp_path / "out", days=1, start=THU, faults=[],
                       roster=tmp_path / "r.json", manifest=tmp_path / "j.json", spaces=["personal"],
                       evals="off", workdir=tmp_path / "fleet")
    opts.roster.write_text(json.dumps(ROSTER))
    opts.manifest.write_text(json.dumps(JOBS))
    fleet = sim.Fleet(opts)
    fleet.build()
    rc, out, _ = fleet.sh(fleet.machines["alpha"], "date -u +%F; python3 -c \"import time, subprocess; "
                          "time.sleep(0.05); subprocess.run(['sleep', '0.1'], timeout=5); print('slept')\"",
                          at(THU, 3))
    assert rc == 0, out
    assert "2026-10-01" in out and "slept" in out, out


@needs_fleet
def test_a_clean_day_is_not_flagged(tmp_path):
    report = _tiny(tmp_path, faults=[])
    assert report["checkpoints"], "no checkpoint ran"
    assert report["breaks"] == [], json.dumps(report["breaks"], indent=1)[:2000]


@needs_fleet
def test_an_injected_usage_limit_is_found_with_its_first_failing_check(tmp_path):
    fault = {"id": "F-limit", "kind": "executor_mode", "machine": "always_on", "job": "alpha-report",
             "mode": "usage_limit", "day": 1, "at": "03:00", "until": [1, "05:00"],
             "desc": "a usage limit on one job"}
    report = _tiny(tmp_path, faults=[fault])
    hits = [b for b in report["breaks"] if b["machine"] == "alpha" and b["subject"] == "alpha-report"]
    assert hits, json.dumps(report["breaks"], indent=1)[:2000]
    assert hits[0]["first_check"], hits[0]
    assert "F-limit" in hits[0]["cause"], hits[0]
    verdict = {f["id"]: f for f in report["faults"]}["F-limit"]
    assert verdict["detected"], verdict
    assert not any(b["machine"] == "beta" for b in report["breaks"]), report["breaks"]


# -- re-running: a comparable summary per run, and a compare step --------------

def _brk(machine, subject, check, cls="baseline", source="job exit", nights=(1,)):
    return {"machine": machine, "source": source, "subject": subject, "first_check": check,
            "first_night": nights[0], "first_ts": "2026-10-01T01:00:00+00:00", "nights": list(nights),
            "class": cls, "cause": "x", "faults": [], "still_red_at_end": True}


def _fault(fid, outcome):
    return {"id": fid, "kind": "executor_mode", "machine": "box", "desc": "d", "outcome": outcome,
            "detected": outcome == "detected", "misbehaviour_ran": 0, "misbehaviour_refused": 0, "breaks": []}


def _report(breaks, faults=()):
    return {"generated": "2026-09-30T00:00:00+00:00", "wall_seconds": 1, "days": 2, "start": "2026-10-01",
            "machines": {"box": {"kind": "server", "jobs": 1}}, "job_runs": 3, "min_interval_s": 3600,
            "daemons_not_simulated": [], "not_modelled": {}, "schedules_not_understood": [], "notes": [],
            "hardcoded_home": [], "sandbox_env": {}, "checkpoints": [], "breaks": list(breaks),
            "faults": list(faults), "promise_evals": {}}


def test_a_break_key_ignores_the_sandbox_path_and_numbers():
    a = _brk("box", "news", "exit 1: cd: /tmp/fleet-sim-bdns_16q/m/box/data/x: No such file (line 12)")
    b = _brk("box", "news", "exit 1: cd: /tmp/fleet-sim-zz9_ab/m/box/data/x: No such file (line 40)")
    assert sim.break_key(a) == sim.break_key(b)
    assert sim.break_key(a) != sim.break_key(_brk("box", "news", "exit 2: something else"))


def test_every_run_writes_a_machine_readable_summary(tmp_path):
    rep = _report([_brk("box", "news", "exit 1"), _brk("box", "inbox", "FATAL", cls="fault")],
                  faults=[_fault("F4", "detected")])
    sim.write_outputs(rep, tmp_path)
    for name in ("report.json", "report.md", "summary.json"):
        assert (tmp_path / name).is_file(), name
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["counts"] == {"baseline": 1, "fault": 1, "unexplained": 0, "total": 2}
    assert s["faults"] == {"F4": "detected"}
    assert len(s["breaks"]) == 2 and all("key" in b and "class" in b for b in s["breaks"])


def test_compare_lists_new_fixed_and_still_red(tmp_path):
    prev, cur = tmp_path / "2026-09-30-7d", tmp_path / "2026-10-01-7d"
    sim.write_outputs(_report([_brk("box", "news", "exit 1"), _brk("box", "inbox", "FATAL", cls="fault")],
                              faults=[_fault("F4", "NOT DETECTED")]), prev)
    sim.write_outputs(_report([_brk("box", "news", "exit 1"), _brk("mac", "drift", "stale", cls="unexplained")],
                              faults=[_fault("F4", "detected")]), cur)
    d = sim.compare_runs(prev, cur)
    assert [b["subject"] for b in d["fixed"]] == ["inbox"]
    assert [b["subject"] for b in d["new"]] == ["drift"]
    assert [b["subject"] for b in d["still"]] == ["news"]
    assert d["faults_changed"] == {"F4": ["NOT DETECTED", "detected"]}
    md = sim.render_compare(d)
    assert "inbox" in md and "drift" in md


def test_compare_reads_a_run_that_has_only_report_json(tmp_path):
    """The first runs (2026-09-30) predate summary.json: compare still works."""
    prev = tmp_path / "old"
    prev.mkdir()
    (prev / "report.json").write_text(json.dumps(_report([_brk("box", "news", "exit 1")])))
    cur = tmp_path / "new"
    sim.write_outputs(_report([]), cur)
    assert [b["subject"] for b in sim.compare_runs(prev, cur)["fixed"]] == ["news"]


def test_runs_land_in_one_folder_and_the_previous_run_is_found(tmp_path):
    first = sim.default_out(tmp_path, days=7, today=dt.date(2026, 9, 30))
    assert first == tmp_path / "2026-09-30-7d"
    sim.write_outputs(_report([]), first)
    again = sim.default_out(tmp_path, days=7, today=dt.date(2026, 9, 30))
    assert again == tmp_path / "2026-09-30-7d-2"
    sim.write_outputs(_report([]), again)
    (tmp_path / "scratch").mkdir()            # not a run: no report.json
    # The container's file system gave both the same mtime: order still holds.
    same = (first / "report.json").stat().st_mtime_ns
    os.utime(again / "report.json", ns=(same, same))
    assert sim.previous_run(tmp_path, again) == first
    assert sim.previous_run(tmp_path, first) is None


def test_a_redirected_jobs_output_is_found_in_the_log_it_appends_to(tmp_path):
    """Once the job list carries the crontab's `>> log 2>&1` (finding 6), a failing
    job's cause is in that log, not on stdout: the break must still quote it."""
    home = tmp_path / "home"
    assert sim.redirect_target("~/x/run.sh >> ~/.datacore/cos/news.log 2>&1", home) == \
        home / ".datacore/cos/news.log"
    assert sim.redirect_target('a 2>&1 | sed "s/^/x /" >> ~/.datacore/cos/v.log', home) == \
        home / ".datacore/cos/v.log"
    assert sim.redirect_target("~/x/run.sh", home) is None
    log = home / ".datacore/cos/news.log"
    log.parent.mkdir(parents=True)
    log.write_text("yesterday ok\n")
    before = sim.log_size(log)
    log.write_text(log.read_text() + "ERROR: MAIL_TRIAGE_ACCOUNTS is not set\n")
    assert sim.appended_since(log, before) == "ERROR: MAIL_TRIAGE_ACCOUNTS is not set\n"


# -- second-run findings (2026-10-01): what the harness itself got wrong -------

class _FakeFleet:
    def __init__(self, machines, jobs, roles):
        self.machines, self._jobs, self.roles = machines, jobs, roles

    def jobs_for(self, m):
        return self._jobs.get(m.name, [])

    def role_machine(self, role):
        return self.roles.get(role)


def _machine(name, kind="server"):
    return sim.Machine(name=name, kind=kind, alias=name, actor=name, manifest_name=name,
                       home=Path("/nonexistent") / name)


def test_the_evals_run_on_the_executor_never_on_the_workstation():
    """Every machine runs a scoreboard since e2acd46; the harness took the first in
    the roster -- the owner's workstation -- so the agent-machine evals judged the
    wrong machine (AGT-11 skipped itself there) and 46 old reds changed key."""
    board = [{"name": "x-promise-scoreboard", "cmd": "python3 ~/Data/.datacore/lib/promise_nightly.py"}]
    machines = {"mac": _machine("mac", "workstation"), "box": _machine("box"), "ns": _machine("ns")}
    week = object.__new__(sim.Week)
    week.o = sim.Options(seed=Path("/x"), out=Path("/x"))
    week.fleet = _FakeFleet(machines, {"mac": board, "box": board, "ns": board},
                            {"executor": "ns", "always_on": "box"})
    assert week._eval_machine().name == "ns"
    week.fleet = _FakeFleet(machines, {"mac": board, "box": board}, {"executor": "ns"})
    assert week._eval_machine().name == "box"


def test_the_roster_keeps_each_machines_setup_profile():
    doc = {"servers": {"claw": {"kind": "server", "setup_profile": "openclaw", "notes": "secret-ish",
                                "access": {"actor": "data", "hostname": "10.0.0.9"}}}}
    s = sim.sanitize_roster(doc)["servers"]["claw"]
    assert s["setup_profile"] == "openclaw"
    assert "notes" not in s and "10.0.0.9" not in json.dumps(s)


@needs_fleet
def test_a_fleet_inside_a_simulated_machine_leaves_the_outer_runs_home_alone(tmp_path, monkeypatch):
    """The audit job runs this very test file inside the week: its tiny fleet
    re-pointed /home/<user> at itself, and from night 1 the box's inbox job ran
    in a pytest scratch fleet (2026-10-01 run, F12's only model call)."""
    monkeypatch.setenv("SIM_ROOT", "/some/outer/fleet")
    opts = sim.Options(seed=SEED, out=tmp_path / "out", days=1, start=THU, faults=[],
                       roster=tmp_path / "r.json", manifest=tmp_path / "j.json", spaces=["personal"],
                       evals="off", workdir=tmp_path / "fleet")
    opts.roster.write_text(json.dumps(ROSTER))
    opts.manifest.write_text(json.dumps(JOBS))
    homes = sorted(Path("/home").glob("*")) if Path("/home").is_dir() else []
    before = {p: (os.readlink(p) if p.is_symlink() else None) for p in homes}
    fleet = sim.Fleet(opts)
    fleet.build()
    after = {p: (os.readlink(p) if p.is_symlink() else None) for p in homes}
    assert after == before
    assert fleet.hardcoded_home is None
    assert any("inside another simulated machine" in n for n in fleet.notes), fleet.notes


@needs_fleet
def test_a_second_fault_on_a_job_already_red_from_the_first_is_still_found(tmp_path):
    """F7 (usage limit on research) made the job red on night 4, but the break was
    grouped with F4's red on night 2 and only F4 got the credit."""
    first = {"id": "F-a", "kind": "executor_mode", "machine": "always_on", "job": "alpha-report",
             "mode": "expired_login", "day": 1, "at": "03:00", "until": [1, "05:00"], "desc": "login"}
    second = {"id": "F-b", "kind": "executor_mode", "machine": "always_on", "job": "alpha-report",
              "mode": "usage_limit", "day": 2, "at": "03:00", "until": [2, "05:00"], "desc": "limit"}
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(ROSTER))
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps(JOBS))
    report = sim.run_week(sim.Options(seed=SEED, out=tmp_path / "out", days=2, start=THU,
                                      faults=[first, second], roster=roster, manifest=manifest,
                                      spaces=["personal"], evals="off", workdir=tmp_path / "fleet"))
    verdict = {f["id"]: f for f in report["faults"]}
    assert verdict["F-a"]["detected"], verdict["F-a"]
    assert verdict["F-b"]["detected"], verdict["F-b"]


PROBE_JOBS = {"version": 1, "jobs": JOBS["jobs"] + [
    {"name": "alpha-probe", "machine": "alpha", "schedule": "0 * * * *",
     "cmd": "mkdir -p ~/.datacore/cos && if timeout 5 bash -c '</dev/tcp/beta/22'; then "
            "echo \"$(date -u +%FT%TZ) beta UP\" >> ~/.datacore/cos/probe.log; else "
            "echo \"$(date -u +%FT%TZ) beta DOWN\" >> ~/.datacore/cos/probe.log; exit 1; fi"}]}


@needs_fleet
def test_an_offline_host_is_seen_by_a_probe_from_another_machine(tmp_path):
    """F6: the fleet probe was left out as unmodellable (no network in the
    sandbox), so a day-long outage could never be detected. Each machine now
    answers on :22 at its roster names while it is online."""
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(ROSTER))
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps(PROBE_JOBS))
    # Night 0 ends 09:00 on day 1: an outage at midday falls in night 1, after
    # a morning of probes that found beta UP.
    fault = {"id": "F-off", "kind": "offline", "machine": "executor", "day": 1, "at": "12:00",
             "until": [1, "16:00"], "desc": "beta offline"}
    report = sim.run_week(sim.Options(seed=SEED, out=tmp_path / "out", days=1, start=THU, faults=[fault],
                                      roster=roster, manifest=manifest, spaces=["personal"], evals="off",
                                      workdir=tmp_path / "fleet"))
    probe = [b for b in report["breaks"] if b["subject"] == "alpha-probe"]
    assert probe, json.dumps(report["breaks"], indent=1)[:2000]
    assert all(b["first_night"] >= 1 for b in probe), probe   # UP before the outage
    assert {f["id"]: f for f in report["faults"]}["F-off"]["detected"], report["faults"]


RESET_JOBS = {"version": 1, "jobs": JOBS["jobs"] + [
    {"name": "beta-triage", "machine": "beta", "schedule": "0 3 * * *",
     "cmd": "cd ~/Data && claude -p --dangerously-skip-permissions 'triage the repos'"}]}


@needs_fleet
def test_an_agent_machine_is_built_by_the_host_setup_and_its_unguarded_run_is_stopped(tmp_path):
    """F10: `git reset --hard` from a `claude -p` started without --settings ran 4/4.
    The real machines carry the guard in hand-edited user settings; the sandbox
    built its machines without the host setup, so it could not show that the
    setup (now) wires it. Machines are built through agent_host_setup.sh."""
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(ROSTER))
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps(RESET_JOBS))
    fault = {"id": "F-reset", "kind": "executor_mode", "machine": "executor", "job": "beta-triage",
             "mode": "reset", "space": "core", "day": 1, "at": "02:00", "until": [1, "04:00"],
             "desc": "reset --hard in core"}
    report = sim.run_week(sim.Options(seed=SEED, out=tmp_path / "out", days=1, start=THU, faults=[fault],
                                      roster=roster, manifest=manifest, spaces=["personal"], evals="off",
                                      workdir=tmp_path / "fleet"))
    settings = tmp_path / "fleet/m/beta/home/.claude/settings.json"
    assert settings.is_file() and "tool_policy_guard.py" in settings.read_text()
    v = {f["id"]: f for f in report["faults"]}["F-reset"]
    assert v["misbehaviour_ran"] == 0 and v["misbehaviour_refused"] >= 1, v
    assert any("host setup" in n for n in report["notes"]), report["notes"]
