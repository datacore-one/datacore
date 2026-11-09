"""Repairs that cannot succeed must stop spending money.

Incident 2026-11-03 (box): about $29 of OpenRouter credit in 8 hours, ended only
by the credit running out. One job, box-ledger-verify, took 24 failed agent runs
and zero successes. Three holes let it through the three-attempt limits:

  1. a run that hit the 600 s timeout was recorded as an infrastructure failure,
     which does not count as an attempt: one item ran 14 times;
  2. while claiming was paused a new repair was filed each day, because the
     breaker counts failed attempts and a pause produces none: three items for
     one job then ran in sequence;
  3. a ledger seal failure, which no commit can change, went to an agent as a
     code fault.

The fourth test is what caused the seal failure: cos_ledger_event.sh wrote as
winston on whichever host ran it, so a run of v2_verify_run.sh on dayshift minted
a new winston key there and forked winston's telemetry log.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
for p in (LIB, LIB / "jobs", LIB / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from test_autofix_repair_breaker import _delegate, _failed_repair, _job, unit  # noqa: E402,F401

OBSERVED_TIMEOUT = ("hermes: executor 'hermes' timed out after 600s: Command "
                    "'[python3]'")


def test_a_timed_out_agent_run_counts_as_an_attempt():
    import ledger_claim
    assert not ledger_claim._infrastructure_failure(OBSERVED_TIMEOUT), (
        "a 10-minute agent run spends real money; it must count towards the three-attempt limit")


@pytest.mark.parametrize("detail", [
    "hermes: binary not found",
    "hermes: unknown executor",
])
def test_a_run_that_never_reached_a_model_still_does_not_count(detail):
    """Control: a host that could not run anything must not dismiss good work."""
    import ledger_claim
    assert ledger_claim._infrastructure_failure(detail)


def test_no_second_repair_is_filed_while_one_for_the_job_is_still_open(unit):  # noqa: F811
    from ledger.log import EventLog
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    EventLog(unit.space, "winston").append("item.create", {
        "id": "autofix-box-x-20261101", "title": "Repair box-x", "assignee": "winston",
        "autofix": True, "job": "box-x", "machine": "box", "contract_sha": "abc",
        "fingerprint": fp})

    state, why = _delegate(unit, job)

    assert unit.appended == [], f"a repair stacked on one still waiting to run: {why}"
    assert state == "exists", (state, why)
    assert "autofix-box-x-20261101" in why, why


def test_an_open_repair_from_before_a_code_change_does_not_block_a_new_one(unit):  # noqa: F811
    """Control: changed code is a new question, and may get its own repair."""
    from ledger.log import EventLog
    job = _job(unit.root)
    old = unit.autofix.fingerprint(job, unit.root)
    EventLog(unit.space, "winston").append("item.create", {
        "id": "autofix-box-x-20261101", "title": "Repair box-x", "assignee": "winston",
        "autofix": True, "job": "box-x", "machine": "box", "contract_sha": "abc",
        "fingerprint": old})
    unit.producer.write_text("#!/bin/sh\necho v2\n")

    state, why = _delegate(unit, job)

    assert state == "delegated", (state, why)


@pytest.mark.parametrize("line", [
    "FAIL 1 of 10 hub space(s) not freshly sealed by their grantor: 2-datacore",
    "FAIL 2-datacore     the newest seal does not verify: behind the seal for: winston.telemetry",
    "hub seal 2-datacore: HubRefused: behind_seal: this copy holds less than a seal",
])
def test_a_ledger_seal_failure_goes_to_a_person(unit, line):  # noqa: F811
    assert unit.autofix.classify_failure(line) == unit.autofix.HOST_STATE


@pytest.mark.parametrize("name", ["box-ledger-verify", "bridge-v2-verify"])
def test_jobs_that_report_the_machines_state_get_no_agent_repair(name):
    from jobs import manifest
    jobs = {j.name: j for j in manifest.load_manifest(LIB / "jobs" / "manifest.yaml")}
    assert jobs[name].repair == "host", (
        f"{name} reports on ledger and fleet state; an agent commit cannot change that")


def _run_event_script(tmp_path, identity: str) -> Path:
    """Run the real cos_ledger_event.sh with `identity` as this host's actor.

    ledger_cli.py is replaced by a recorder, so the test sees whether a write
    was attempted without touching any real ledger or key."""
    script = ROOT / ".datacore/modules/chief-of-staff/server/lib/cos_ledger_event.sh"
    assert script.exists(), script
    home = tmp_path / "home"
    (home / ".datacore").mkdir(parents=True)
    (home / ".datacore" / "identity.env").write_text(f"DATACORE_ACTOR={identity}\n")
    space = tmp_path / "space"
    (space / ".datacore" / "events").mkdir(parents=True)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "actor_identity.py").write_text((LIB / "actor_identity.py").read_text())
    marker = tmp_path / "wrote"
    (lib / "ledger_cli.py").write_text(
        f"import sys\nopen({str(marker)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATACORE")}
    env.update(HOME=str(home), DATACORE_LEDGER_SPACE=str(space), DATACORE_LIB=str(lib))
    done = subprocess.run(["bash", str(script), "v2-verify", "failed", "7 checks"],
                          env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return marker


def test_another_host_never_writes_as_winston(tmp_path):
    marker = _run_event_script(tmp_path, "dayshift")
    assert not marker.exists(), (
        "on dayshift this minted a new winston key and forked winston's log (2026-11-03)")


def test_winstons_own_host_still_records_its_jobs(tmp_path):
    """Control: liveness is measured from this event on the box."""
    marker = _run_event_script(tmp_path, "winston")
    assert marker.exists(), "winston's own job events stopped being recorded"
    assert "--actor winston" in marker.read_text()
