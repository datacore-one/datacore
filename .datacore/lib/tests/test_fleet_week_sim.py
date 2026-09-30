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
    tick = sim.parse_schedule("continuous (venture-heartbeat.service, one tick every 1800 s)")
    assert tick.kind == "interval" and len(sim.fires(tick, THU, min_interval_s=3600)) == 24
    assert sim.parse_schedule("tris-heartbeat.timer (every 30 min)").kind == "interval"
    assert sim.parse_schedule("continuous (launchd KeepAlive daemon, RunAtLoad)").kind == "daemon"
    assert sim.parse_schedule("continuous (datacore-agent-stream-tail sidecar)").kind == "daemon"


def test_a_visitor_runs_only_while_awake_and_catches_up_at_wake():
    join = sim.parse_schedule("on join: visitor_join.py runs it", trigger="join")
    assert sim.fires(join, THU, visitor=True) == [at(THU, h) for h in (8, 12, 16, 20)]
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
