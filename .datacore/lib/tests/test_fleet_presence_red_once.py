"""A promised-up host that stays down turns a check red and is alerted ONCE (fleet sim 2026-10-03, F6).

The harsh fleet week took one agent host (plur-claw) off the network for a day. The
box's fleet probe logged DOWN every 15 minutes and UP when it came back, but its
contract (`regex " UP "` over the whole log) stayed green because the other hosts
answered, and the simulator credited nothing to the outage. A host the roster
promises is always on (kind server) that is down longer than a grace period must:

- turn the probe's own contract red (the manifest's artifact check, run as job_verify
  runs it), for as long as it stays down;
- send exactly one alert naming it, and one when it is back;
- not alert, and not go red, for a single missed sample inside the grace period.

Runs the real cos_fleet_probe.sh with the sandbox, stubs and simulated clock of the
OPS-14 promise eval (imported, not modified).
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(LIB / "tests"))

from test_promise_OPS14_quiet_machine_reported_from_roster import (  # noqa: E402
    START, _monitor_jobs, _names, _sandbox, _timeline)
from jobs.checks import run_check  # noqa: E402
from jobs.manifest import Artifact  # noqa: E402

JOB = next(j for j in _monitor_jobs() if j["name"] == "box-fleet-probe")


def _contract_errors(sb, at: dt.datetime) -> list[str]:
    """The manifest's checks for box-fleet-probe, read at simulated time `at`."""
    errors = []
    log = sb["home"] / ".datacore" / "cos" / "fleet-probe.log"
    for raw in JOB["artifacts"]:
        art = Artifact(**{k: raw.get(k) for k in ("path", "check", "arg", "max_age_hours")})
        art.path = str(log)
        errors += run_check(art, now=log.stat().st_mtime + 60)
    return errors


def _alerts_about(alerts, host):
    return [(at, m) for at, m in alerts if host in _names(m)]


def test_a_day_down_is_red_and_alerted_once(tmp_path):
    sb = _sandbox(tmp_path)
    down_from, back_at = START + dt.timedelta(hours=1), START + dt.timedelta(hours=7)
    alerts = _timeline(sb, JOB, lambda h, t: h != "gamma" or not (down_from <= t < back_at),
                       START, 5)
    errs = _contract_errors(sb, START + dt.timedelta(hours=5))
    assert errs, "gamma has been down 4 hours and the fleet probe's contract is still green"
    assert any("gamma" in e for e in errs), f"the red check does not name the host: {errs}"
    down = [m for _, m in _alerts_about(alerts, "gamma")]
    assert len(down) == 1, f"expected one alert about gamma while it is down, got {len(down)}: {down}"


def test_recovery_is_alerted_once_and_the_check_turns_green(tmp_path):
    sb = _sandbox(tmp_path)
    down_from, back_at = START + dt.timedelta(hours=1), START + dt.timedelta(hours=4)
    alerts = _timeline(sb, JOB, lambda h, t: h != "gamma" or not (down_from <= t < back_at),
                       START, 6)
    about = [m for _, m in _alerts_about(alerts, "gamma")]
    assert len(about) == 2, f"expected one DOWN and one back-UP alert, got: {about}"
    assert not _contract_errors(sb, START + dt.timedelta(hours=6)), "gamma is back and the check is still red"


def test_one_missed_sample_is_neither_red_nor_alerted(tmp_path):
    sb = _sandbox(tmp_path)
    blip = START + dt.timedelta(hours=1)
    seen = []

    def up(h, t):
        return h != "gamma" or t != blip

    alerts = _timeline(sb, JOB, up, START, 1)   # the run ends ON the missed sample
    assert not _alerts_about(alerts, "gamma"), "a single missed sample paged the owner"
    assert not _contract_errors(sb, blip), "a single missed sample turned the check red"


def test_every_host_up_is_green(tmp_path):
    sb = _sandbox(tmp_path)
    alerts = _timeline(sb, JOB, lambda h, t: True, START, 1)
    assert not alerts
    assert not _contract_errors(sb, START + dt.timedelta(hours=1))
