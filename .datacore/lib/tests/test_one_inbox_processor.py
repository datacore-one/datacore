"""One scheduled job processes the inbox, and its contract is the only one.

On 2026-09-17 the overnight host's `/process-inbox` timer was retired (the
box's `cos_inbox.sh` runs the same workflow at the same hour; two agents
routing one inbox at 05:00 race each other). Its contract stayed in the
manifest. From then on job_verify judged a job that never runs: red whenever
its artifact aged past 26h, and green only on nights when an unrelated job (the
02:00 research run) happened to rewrite the same file. A contract that goes
green by coincidence is worse than none -- it cannot tell a working job from a
dead one.
"""
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
MANIFEST = LIB / "jobs" / "manifest.yaml"
MODULE = LIB.parent / "modules" / "nightshift" / "module.yaml"


def _jobs():
    return yaml.safe_load(MANIFEST.read_text()).get("jobs", [])


def _processes_inbox(job):
    cmd = job.get("cmd") or ""
    return "--command=/process-inbox" in cmd or cmd.endswith("cos_inbox.sh")


def test_exactly_one_job_processes_the_inbox():
    names = [j["name"] for j in _jobs() if _processes_inbox(j)]
    assert len(names) == 1, f"inbox processors in the manifest: {names}"


def test_retired_overnight_inbox_job_has_no_contract():
    names = {j["name"] for j in _jobs()}
    assert "nightshift-inbox-process" not in names
    if MODULE.exists():
        owned = [s.get("name") for s in yaml.safe_load(MODULE.read_text()).get("schedules", [])]
        assert "nightshift-inbox-process" not in owned
