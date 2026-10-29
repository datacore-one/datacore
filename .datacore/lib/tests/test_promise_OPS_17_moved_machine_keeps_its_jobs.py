"""OPS-17: When a machine is moved, replaced or renamed, every job it ran is running on
the new one by the next morning, or I am told exactly which ones are not.

Kind: deterministic. A throwaway install (tmp DATACORE_ROOT) whose roster says what the
real one says after Winston's move into a pod (10-25/26): the always-on machine is
still `box` in the job list and the roster, but the host now identifies itself as
`winston` (its actor, its hostname, its ssh alias). Its job list holds two `box` jobs
whose artifacts are missing -- exactly what backup and setup-verify looked like after
the move, when they stopped writing.

Evidence guarded (findings-winston-state S14-S16, S40): after the move v2-verify said
"no job declared for machine 'winston' -- nothing checked" (v2_verify.py:195-217 takes
the machine name from DATACORE_ACTOR / the hostname and job_verify matches it
literally against `machine:` in the manifest), so the backup and setup-verify jobs
stopped silently for three and a half days. job_verify itself answered
"OK 0 jobs 0 artifacts" for the new name: a pass-shaped line about nothing.

Graded:
1. job_verify run under the machine's new name judges the jobs the roster says that
   machine runs (both are named, as failing), instead of "OK 0 jobs".
2. v2_verify's job-contract check on the renamed host either judges those jobs or
   names each job that is not being checked -- never "no job declared ... nothing
   checked" with no job named.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import job_verify

JOBS = ("box-backup", "box-setup-verify")


def _install(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "Data"
    reg = root / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "infrastructure.yaml").write_text(yaml.safe_dump({
        "roles": {"always_on": "box", "sequencer": "winston", "console": "mac"},
        "servers": {
            "box": {"kind": "server", "ssh_alias": "winston", "ledger_actors": ["winston"],
                    "access": {"hostname": "winston", "actor": "winston", "data_root": "~/Data"}},
            "mac": {"kind": "workstation", "ssh_alias": "-", "ledger_actors": ["mac"],
                    "access": {"hostname": "desk", "actor": "mac", "data_root": "~/Data"}},
        }}, sort_keys=False))
    jobs_dir = root / ".datacore" / "lib" / "jobs"
    jobs_dir.mkdir(parents=True)
    gone = tmp_path / "never-written"
    manifest = jobs_dir / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [
        {"name": name, "machine": "box", "schedule": "30 2 * * *", "cmd": "true", "on_fail": "log",
         "delegate": False, "artifacts": [{"path": str(gone / f"{name}.log"), "max_age_hours": 26}]}
        for name in JOBS] + [
        {"name": "mac-other", "machine": "mac", "schedule": "0 3 * * *", "cmd": "true", "on_fail": "log",
         "artifacts": [{"path": str(gone / "mac-other.log")}]}]}, sort_keys=False))
    space = tmp_path / "space"
    space.mkdir()
    return root, space


def _env(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setenv("DATACORE_ACTOR", "winston")
    monkeypatch.setattr(job_verify, "DATACORE_ROOT", root)
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: True)
    monkeypatch.setattr(job_verify, "_file_task", lambda *a, **k: None)


def test_the_checker_judges_the_moved_machines_jobs_under_its_new_name(tmp_path, monkeypatch, capsys):
    root, space = _install(tmp_path)
    _env(monkeypatch, root)
    manifest = root / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    try:
        job_verify.main(["--machine", "winston", "--manifest", str(manifest), "--space", str(space),
                         "--alert", "log"])
    except SystemExit:
        pass
    cap = capsys.readouterr()
    out = cap.out + cap.err
    unjudged = [j for j in JOBS if f"'{j}'" not in out]
    assert not unjudged, (
        f"after the machine was renamed to 'winston' (roster: box's actor and hostname), its jobs "
        f"{', '.join(unjudged)} were not judged at all -- job_verify said: {out.strip()[:160]!r}")


def test_v2_verify_names_the_jobs_a_renamed_machine_stopped_checking(tmp_path, monkeypatch):
    root, _space = _install(tmp_path)
    _env(monkeypatch, root)
    import v2_verify
    rep = v2_verify.Report()
    v2_verify.check_jobs(rep)
    rows = [c for c in rep.checks if c.dip == "0035"]
    assert rows, "v2_verify reported no job-contract row at all"
    row = rows[0]
    judged = row.ok is not None
    named = all(j in row.detail for j in JOBS)
    assert judged or named, (
        f"after the move v2_verify judged none of the machine's jobs and named none of them: "
        f"{row.name}: {row.detail!r}")
