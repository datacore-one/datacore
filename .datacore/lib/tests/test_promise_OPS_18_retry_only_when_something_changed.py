"""OPS-18: a failed run is tried again only when something that could change the outcome has changed.

The owner's words: "A failed run is tried again only when something that could
change the outcome has changed: a limit with a known reset is waited out, a job
blocked by a problem it already reported says it is waiting instead of running
again, and a repair is judged after the job's next run, never counting an
attempt that could not reach a model."

Evidence (2026-10-28 fleet log review,
2-datacore/1-tracks/ops/reports/2026-10-28-fleet-logs-bug-classes-report.md):

  1. visitor_join.py:95 and :269-271 re-run a red arrival duty (mac-suite-audit)
     every RETRY_S["arrival"] = 7200 s with no cap and no check that anything
     changed: 69 runs, 50.8 laptop-hours in 8 days against the same red tests.
  2. jobs/fix_check.py passes a repair only if the job's output is good right
     now. A daily job's repair is checked before the job has run again, so it
     reads the morning's (pre-repair) output, fails, and three such checks
     dead-letter a repair nobody could judge yet (14 on Winston).
  3. ledger_claim counts a release as an attempt unless its error is a known
     infrastructure or usage-limit failure; "hermes_oneshot: incomplete
     provider result" (the model never answered) is neither, so attempts that
     never reached a model count towards the dead-letter.

Hermetic: tmp_path and the per-test DATACORE_STATE only; no duty, job_verify,
model or network is run -- each is replaced by a stub that answers the way the
real one did in the evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

import visitor_join as vj
from jobs import awake

NOW = 1_800_000_000.0


# ── 1. a red duty is not re-run while nothing that could change it changed ──

@pytest.fixture
def _join_state(tmp_path, monkeypatch):
    monkeypatch.setattr(vj, "ROOT", tmp_path / "Data")
    (tmp_path / "Data").mkdir()
    monkeypatch.setattr(vj, "RECORD", tmp_path / "join.json")
    monkeypatch.setattr(vj, "ATTEMPT", tmp_path / "join-attempt.json")
    monkeypatch.setattr(vj, "LOG", tmp_path / "join.log")
    monkeypatch.setattr(vj, "DUTIES", tmp_path / "join-duties.json")
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake, "always_on", lambda machine, roster=None: False)
    monkeypatch.setattr(awake, "in_dark_wake", lambda **kw: False)
    monkeypatch.setattr(vj, "gates", lambda **kw: {})
    monkeypatch.setattr(vj, "duties", lambda triggers, **kw: [
        n for n, t in (("mac-suite-audit", "arrival"),) if t in triggers])
    vj.RECORD.write_text(json.dumps({"joined_at": NOW - 600, "converged": True}))
    return tmp_path


def test_a_red_suite_audit_is_not_rerun_every_two_hours_when_nothing_changed(_join_state, monkeypatch):
    """The suite audit is red because tests fail. No code, test or input changed
    all day, so re-running it can only give the same answer: at most one retry
    in a waking day, not one every two hours."""
    clock = {"t": NOW}
    ran: list[float] = []

    def run_duty(name):
        ran.append(clock["t"])
        return {"rc": 1, "at": clock["t"], "seconds": 900.0,
                "last": "suite audit: 5 suites not green, 59 unexplained"}
    monkeypatch.setattr(vj, "run_duty", run_duty)
    vj.DUTIES.write_text(json.dumps({"mac-suite-audit": {
        "rc": 1, "at": NOW, "seconds": 900.0,
        "last": "suite audit: 5 suites not green, 59 unexplained"}}))

    # A laptop open for a working day: launchd ticks every five minutes.
    for step in range(1, 12 * 14 + 1):
        clock["t"] = NOW + step * 300
        vj.retry_failed(now=clock["t"])

    assert len(ran) <= 1, (
        f"mac-suite-audit was re-run {len(ran)} times in 14 waking hours against the same red "
        f"result with nothing changed (visitor_join.retry_failed every RETRY_S['arrival']="
        f"{vj.RETRY_S['arrival']}s, no cap, no change check)")


# ── 2. a repair is judged after the job's next run ─────────────────────────

def _daily_job(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "scoreboard.log"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [{
        "name": "box-daily", "machine": "box", "schedule": "20 7 * * *", "cmd": "true",
        "artifacts": [{"path": str(out), "check": "regex", "arg": "level=",
                       "max_age_hours": 26}]}]}, sort_keys=False))
    return manifest, out


def _fake_job_verify(manifest: Path, monkeypatch):
    """job_verify judged by the real artifact checks against the tmp manifest,
    printed the way job_verify prints a failure."""
    from jobs.checks import run_check
    from jobs.manifest import Artifact
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        if not any(str(c).endswith("job_verify.py") for c in cmd):
            return real_run(cmd, *a, **kw)
        job = yaml.safe_load(manifest.read_text())["jobs"][0]
        fails = [e for raw in job["artifacts"] for e in run_check(Artifact(**raw))]
        err = "".join([f"job '{job['name']}' FAILED:\n"] + [f"  - {f}\n" for f in fails]) if fails else ""
        return subprocess.CompletedProcess(cmd, 1 if fails else 0,
                                           "" if fails else "OK 1 jobs 1 artifacts\n", err)
    monkeypatch.setattr(subprocess, "run", run)


def _fix_check(manifest: Path, sha: str, monkeypatch) -> int:
    from jobs import fix_check
    monkeypatch.setattr(sys, "argv", ["fix_check.py", "--job", "box-daily", "--machine", "box",
                                      "--contract-sha", sha, "--manifest", str(manifest)])
    return fix_check.main()


def test_a_daily_jobs_repair_is_not_failed_before_the_job_has_run_again(tmp_path, monkeypatch, capsys):
    """Repaired at 14:00; the job runs at 07:20 tomorrow. Its artifact is this
    morning's bad output, written before the repair. Checking it now cannot
    say whether the repair worked, so it must not be a failed attempt (exit 1
    is what ledger_claim counts towards the dead-letter). After the job's next
    run the same check judges it."""
    from jobs import fix_check
    manifest, out = _daily_job(tmp_path)
    out.write_text("scoreboard crashed before writing a level\n")
    morning = time.time() - 7 * 3600
    os.utime(out, (morning, morning))
    sha = fix_check.contract_sha("box-daily", manifest)
    _fake_job_verify(manifest, monkeypatch)

    rc = _fix_check(manifest, sha, monkeypatch)
    err = capsys.readouterr().err
    assert rc != 1, (
        "fix_check judged a repair as failed (exit 1) from output the job wrote before the "
        f"repair -- the job has not run since, so this attempt cannot be judged yet: {err.strip()[:200]}")

    # The job's next run: still bad. NOW the repair is judged, and it failed.
    out.write_text("scoreboard crashed before writing a level\n")
    later = time.time() + 5
    os.utime(out, (later, later))
    assert _fix_check(manifest, sha, monkeypatch) == 1, \
        "after the job's next run a still-failing artifact must fail the repair"


# ── 3. an attempt that could not reach a model is not counted ──────────────

def test_an_incomplete_provider_result_is_not_counted_as_an_attempt():
    """hermes_oneshot exits 1 with "incomplete provider result" when the provider
    never produced an answer. That is the model being unreachable, not the task
    failing; counting it dead-letters good work."""
    import claim_backoff
    import ledger_claim
    detail = "hermes: hermes exited 1: hermes_oneshot: incomplete provider result"
    not_counted = (ledger_claim._infrastructure_failure(detail)
                   or claim_backoff.is_usage_limit(detail))
    assert not_counted, (
        "ledger_claim releases 'hermes_oneshot: incomplete provider result' with no kind, so it "
        "counts towards MAX_ATTEMPTS although no model was reached (claim_backoff/"
        "INFRASTRUCTURE_MARKERS do not recognise it)")
