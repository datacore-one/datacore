"""A fault whose alarm was already ringing is "cannot tell", not "NOT DETECTED".

Week run of 2026-11-20: "one agent host offline for a day" and "the inbox agent
says it is done and processes nothing" were both scored NOT DETECTED. Both
alarms did fire (the fleet probe said the host was DOWN; the inbox job said the
model claimed done and moved nothing), but they had been red since the first
night for sandbox reasons, so the fault added no new red check. Reading that as
"the system does not notice" was wrong, and the report invited it.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import fleet_week_sim as sim  # noqa: E402

PROBE = {"class": "baseline", "machine": "box", "subject": "box-fleet-probe",
         "first_check": "exit 1: fleet presence: RED -- hermes DOWN for 1.0h; plur-claw DOWN for 1.0h"}
INBOX = {"class": "baseline", "machine": "box", "subject": "box-inbox",
         "first_check": "inbox.log: the run left 'fatal:' in its output"}
OTHER = {"class": "baseline", "machine": "box", "subject": "box-news", "first_check": "news: FAILED"}


def test_an_offline_host_the_probe_already_called_down_is_masked():
    s = {"kind": "offline", "target": "plur-claw"}
    assert sim.masked_by(s, [OTHER, PROBE]) == "box:box-fleet-probe"


def test_a_job_fault_on_a_job_already_red_is_masked():
    s = {"kind": "executor_mode", "target": "box", "job": "inbox$", "mode": "claim_done"}
    assert sim.masked_by(s, [OTHER, INBOX]) == "box:box-inbox"


def test_an_unrelated_red_check_masks_nothing():
    assert sim.masked_by({"kind": "offline", "target": "plur-claw"}, [OTHER, INBOX]) is None
    assert sim.masked_by({"kind": "executor_mode", "target": "box", "job": "research"}, [OTHER, INBOX]) is None
    assert sim.masked_by({"kind": "executor_mode", "target": "nightshift", "job": "inbox$"}, [INBOX]) is None


def test_only_checks_red_before_any_fault_count():
    fault_break = dict(PROBE, **{"class": "fault"})
    assert sim.masked_by({"kind": "offline", "target": "plur-claw"}, [fault_break]) is None
