"""MSG-11: One problem reaches me as one item, however many checks notice it, and when
one cause stops many jobs -- a usage limit, a machine offline -- I get one message
naming it and the jobs it stopped.

Kind: deterministic. The real verifier (job_verify.main) over a tmp manifest of three
jobs on one machine, all failing for ONE cause: the machine's own state (here, its disk
mount is gone), which the repairer classifies as a host-state fault that no agent can
fix and hands to the owner. The repair hand-off is a stand-in returning what
jobs/autofix.delegate returns for that case; the Telegram send is captured, never made.

Evidence guarded (findings-winston-state S29/S30, satellites): job_verify dispatches
"exactly one alert for one failing job" (job_verify.py _dispatch_alert), and
job_verify_notify.sh relays one block per job, so one cause that stopped many jobs
arrived as many messages -- one usage limit sent 5-8 separate messages a morning
(19 in the week).

Graded: one run in which three jobs on one machine fail for the same cause sends ONE
message, and that message names all three jobs.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import job_verify

JOBS = ("box-backup", "box-checkpoint", "box-setup-verify")
CAUSE = "the data disk is not mounted on box"


def _main(argv: list[str]) -> int:
    try:
        job_verify.main(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def test_one_host_fault_that_stops_three_jobs_is_one_message_naming_them(tmp_path, monkeypatch):
    import jobs.recurrence as R
    monkeypatch.setattr(R, "STATE", tmp_path / "rec.json")
    gone = tmp_path / "mnt" / "data"                      # the mount that disappeared
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [
        {"name": n, "machine": "box", "schedule": "30 2 * * *", "cmd": "true", "on_fail": "telegram",
         "artifacts": [{"path": str(gone / f"{n}.log")}]} for n in JOBS]}, sort_keys=False))
    space = tmp_path / "space"
    space.mkdir()
    monkeypatch.setenv("DATACORE_ACTOR", "test-actor")

    sent: list[str] = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(job_verify, "_file_task", lambda *a, **k: None)
    monkeypatch.setattr(job_verify, "_delegate_repair", lambda job, failures, rec: (
        "escalate", f"{job.name} on {job.machine} is failing on the HOST's state ({CAUSE}), which no agent "
                    f"repair in a fresh checkout can change; it needs a person (the owner) on {job.machine}"))

    assert _main(["--machine", "box", "--manifest", str(manifest), "--space", str(space)]) == 1

    assert len(sent) == 1, (
        f"one cause ({CAUSE}) stopped {len(JOBS)} jobs and the owner got {len(sent)} separate messages, "
        f"one per job: {[m.splitlines()[0][:90] for m in sent]}")
    missing = [j for j in JOBS if j not in sent[0]]
    assert not missing, f"the one message does not name every job the cause stopped: missing {missing}"
