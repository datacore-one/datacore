"""OPS-15: a job added today is not reported failing before it has had its first chance to run.

2026-10-28: box-delegation-review was declared at 10:28 UTC (first run 23:30).
Winston's job_verify judged its artifact within the hour -- a file from
2026-10-25, "stale (66.68h)" -- and the owner was alerted about a job that had
never been due. A job is judged only once its freshness window (the longest
max_age_hours of its artifacts) has passed since the verifier first saw it.

Installing this must not hide anything already declared: the first run of the
new bookkeeping treats every job it already knows as old.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

import job_verify


def _manifest(tmp_path: Path, names: list[str]) -> Path:
    stale = tmp_path / "old-artifact.json"
    stale.write_text('{"status": "complete"}')
    old = time.time() - 70 * 3600
    os.utime(stale, (old, old))
    jobs = [{"name": n, "machine": "box", "schedule": "30 23 * * *", "cmd": "true",
             "artifacts": [{"path": str(stale), "max_age_hours": 26}]} for n in names]
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "jobs": jobs}, sort_keys=False))
    return p


def _run(manifest: Path, tmp_path: Path) -> int:
    try:
        space = tmp_path / "space"
        space.mkdir(exist_ok=True)
        job_verify.main(["--machine", "box", "--manifest", str(manifest), "--alert", "log",
                         "--space", str(space)])
    except SystemExit as e:
        return e.code or 0
    return 0


def test_a_job_declared_after_the_verifier_started_is_not_failed_before_its_window(tmp_path, capsys):
    assert _run(_manifest(tmp_path, ["existing-job"]), tmp_path) == 1     # known job: judged
    capsys.readouterr()
    code = _run(_manifest(tmp_path, ["existing-job", "brand-new-job"]), tmp_path)
    err = capsys.readouterr().err
    assert "job 'existing-job' FAILED" in err                             # still judged
    assert "job 'brand-new-job' FAILED" not in err                        # not yet due
    assert "brand-new-job" in err and "first" in err.lower()              # said, not hidden


def test_the_window_ends_and_the_new_job_is_judged(tmp_path, capsys, monkeypatch):
    _run(_manifest(tmp_path, ["existing-job"]), tmp_path)
    _run(_manifest(tmp_path, ["existing-job", "brand-new-job"]), tmp_path)
    capsys.readouterr()
    later = time.time() + 27 * 3600
    monkeypatch.setattr(job_verify.time, "time", lambda: later)
    _run(_manifest(tmp_path, ["existing-job", "brand-new-job"]), tmp_path)
    assert "job 'brand-new-job' FAILED" in capsys.readouterr().err
