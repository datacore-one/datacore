"""V4 (PLAN Phase 2; audit C8): the daily ledger check covers every space's
events -- task logs and telemetry logs -- and its alert contract bites on a
planted bad event.

Until 2026-09-26 the daily, alerted "ledger verify" ran
`ledger_cli.py verify --space ~/Data`: the root's gitignored telemetry folder
("OK 2 files 1734 events"), never any space. It was green by construction.
The existing OPS-3 eval proves the daily script loops over the spaces, with a
STUB verifier. This one runs the REAL verifier on real (disposable) logs, then
judges the job's real alert contract from the manifest:

  * the job declared for the daily ledger verify artifact runs the script
    exercised here (not a single-directory verify);
  * a clean installation -> the job passes and its contract holds;
  * one tampered event in one space's task log -> the job fails, names the
    space, and the contract FAILS (the alert fires);
  * the same for one tampered event in a space's telemetry log (decision 3:
    telemetry is a separate log per space, still history).

Seeded failure: the old command (verify of the root folder only), or a daily
check that skips telemetry logs.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import _verify_fixtures as vf

LIB = vf.LIB
REAL = ("ledger_daily.sh", "runtime_shell.sh", "spaces.py", "yaml_safety.py", "ledger_cli.py")


def _daily_job():
    sys.path.insert(0, str(LIB))
    from jobs.manifest import load_manifest
    jobs = load_manifest(LIB / "jobs" / "manifest.yaml")
    found = [j for j in jobs if any(a.path.endswith("/ledger-verify.log") for a in j.artifacts)]
    assert found, "no job in the manifest is contracted on the daily ledger verify artifact (ledger-verify.log)"
    return found


def _installation(root: Path, tmp_path: Path):
    for name in ("0-alpha", "1-beta", "2-gamma"):
        space = vf.new_space(root, name)
        vf.append(space, "alice", 3)
        vf.append(space, "bot", 3)
    from ledger.log import EventLog
    log = EventLog(root / "2-gamma", "bot")
    for i in range(3):
        log.append("metric.attest", {"metric": "probe.latency", "value": i, "unit": "ms"})
    assert vf.log_path(root / "2-gamma", "bot", telemetry=True).is_file()
    # The root's own telemetry folder exists too, as on a real install: it is not a space.
    (root / ".datacore" / "events").mkdir(parents=True, exist_ok=True)


def _run_daily(root: Path, tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    for f in REAL:
        (scripts / f).symlink_to(LIB / f)
    # Not what V4 judges: the drift check and the checkpoint have their own evals.
    (scripts / "shadow_check.py").write_text("print('drift 0')\n")
    (scripts / "ledger_checkpoint.py").write_text("print('OK checkpoint')\n")
    state = tmp_path / "state"
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state), DATACORE_PYTHON=sys.executable)
    proc = subprocess.run(["bash", str(scripts / "ledger_daily.sh")], env=env, capture_output=True,
                          text=True, timeout=300)
    log = state / "ledger-verify.log"
    return proc, (log.read_text() if log.exists() else ""), log


def _contract(job, log: Path) -> list[str]:
    from jobs.checks import run_check
    failures = []
    for a in job.artifacts:
        if a.path.endswith("/ledger-verify.log"):
            failures += run_check(replace(a, path=str(log)))
    return failures


def test_the_daily_job_runs_the_every_space_check():
    for job in _daily_job():
        script = Path(str(job.cmd).split()[0]).name if job.cmd else ""
        assert script == "ledger_daily.sh", (
            f"job '{job.name}' is contracted on the daily verify artifact but runs '{job.cmd}', "
            "not the every-space daily check this eval exercises")
        assert "--space" not in str(job.cmd), f"job '{job.name}' verifies one directory: {job.cmd}"


def test_a_clean_installation_passes_and_the_contract_holds(sandbox_root, tmp_path):
    _installation(sandbox_root, tmp_path)
    proc, out, log = _run_daily(sandbox_root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in ("0-alpha", "1-beta", "2-gamma"):
        assert f"{name}: OK" in out, f"no per-space result for {name}:\n{out}"
    for job in _daily_job():
        assert _contract(job, log) == [], f"clean run, contract of '{job.name}' should hold:\n{out}"


@pytest.mark.parametrize("where", ["task log", "telemetry log"])
def test_a_planted_bad_event_fails_the_job_and_fires_the_alert(sandbox_root, tmp_path, where):
    _installation(sandbox_root, tmp_path)
    target = vf.log_path(sandbox_root / "2-gamma", "bot", telemetry=(where == "telemetry log"))
    vf.tamper_payload(target, 1)
    proc, out, log = _run_daily(sandbox_root, tmp_path)
    assert proc.returncode != 0, f"a bad event in a {where} must fail the daily check:\n{proc.stdout}{proc.stderr}"
    assert any("2-gamma" in line and "FAIL" in line for line in out.splitlines()), (
        f"the failing space must be named:\n{out}")
    assert any(line.strip().startswith("1-beta: OK") for line in out.splitlines()), (
        f"the other spaces are still checked and reported:\n{out}")
    for job in _daily_job():
        failures = _contract(job, log)
        assert failures, (f"a bad event in a {where} left the alert contract of '{job.name}' green "
                          f"(it would not alert):\n{out}")
