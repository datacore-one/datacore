"""NS-10: A repair counts as fixed only when the failing job's own check passes again. An open or merged pull request alone does not count.

Kind: deterministic. Runs the real `jobs/fix_check.py` (the done-condition a
repair item carries) against a fake `gh` on PATH and a tmp manifest, and calls
the real `autofix.escalations` (what decides whether a closed repair is still
news) with the ledger reading stood in for.

Seeded failure: a repair whose only evidence is a pull request -- OPEN, then
MERGED -- naming the item, while the job's own verification still fails; and a
repair item closed as done whose job never verified afterwards. The promise
holds when neither pull request passes the done-check, the job's own passing
check does, and a done repair with the job still failing is still reported.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import fix_check  # noqa: E402

FIX_CHECK = LIB / "jobs" / "fix_check.py"
ITEM = "autofix-box-x-20260926"


def _fake_gh(tmp_path, state):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "prs.json").write_text(json.dumps([{
        "number": 7, "title": f"fix box-x ({ITEM})", "body": "", "state": state,
        "mergedAt": "2026-09-26T10:00:00Z" if state == "MERGED" else None,
        "url": "https://github.com/o/r/pull/7"}]))
    (b / "files.json").write_text(json.dumps({"files": [{"path": "lib/producer.py"}]}))
    (b / "gh").write_text(f"#!/bin/bash\ncase \"$2\" in list) cat {b}/prs.json;; "
                          f"view) cat {b}/files.json;; *) exit 9;; esac\n")
    (b / "gh").chmod(0o755)
    return b


def _manifest(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump({"jobs": [{
        "name": "box-x", "machine": "box", "schedule": "0 * * * *", "cmd": "true",
        "exit_ok": [0], "on_fail": "log",
        "artifacts": [{"path": "~/x.log", "check": "regex", "arg": "^OK$"}]}]}))
    return p


def _pr_only(tmp_path, state):
    """The done-check a cross-host repair item carries, with only a PR as evidence."""
    m = _manifest(tmp_path)
    sha = fix_check.contract_sha("box-x", m)
    env = {**os.environ, "PATH": f"{_fake_gh(tmp_path, state)}:{os.environ['PATH']}"}
    return subprocess.run([sys.executable, str(FIX_CHECK), "--job", "box-x", "--machine", "box",
                           "--contract-sha", sha, "--manifest", str(m), "--stage", "merged",
                           "--item", ITEM, "--repo", "o/r"],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60)


def test_an_open_pull_request_alone_does_not_count_as_fixed(tmp_path):
    r = _pr_only(tmp_path, "OPEN")
    assert r.returncode != 0, f'an OPEN pull request passed the done-check: {r.stdout.strip()}'


def test_a_merged_pull_request_alone_does_not_count_as_fixed(tmp_path):
    r = _pr_only(tmp_path, "MERGED")
    assert r.returncode != 0, f'a MERGED pull request passed the done-check: {r.stdout.strip()}'


def _verify_stage(tmp_path, monkeypatch, job_verify_output):
    m = _manifest(tmp_path)
    sha = fix_check.contract_sha("box-x", m)
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if any(str(c).endswith("job_verify.py") for c in cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout=job_verify_output, stderr="")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(fix_check.subprocess, "run", fake)
    monkeypatch.setattr(sys, "argv", ["fix_check.py", "--job", "box-x", "--machine", "box",
                                      "--contract-sha", sha, "--manifest", str(m)])
    return fix_check.main()


def test_the_jobs_own_check_decides(tmp_path, monkeypatch):
    assert _verify_stage(tmp_path, monkeypatch,
                         "job 'box-x' artifact ~/x.log: regex ^OK$ not found\n") == 1
    assert _verify_stage(tmp_path, monkeypatch, "OK 19 jobs 19 artifacts\n") == 0


def test_a_repair_closed_done_while_its_job_still_fails_is_not_fixed(monkeypatch):
    import autofix
    now = 1_800_000_000_000.0
    monkeypatch.setattr(autofix, "_acked", lambda: set())
    monkeypatch.setattr(autofix, "recovered_since", lambda root, job, closed: False)
    monkeypatch.setattr(autofix, "repairs", lambda root: [{
        "id": ITEM, "status": "dismissed", "closed_kind": "done",
        "closed_reason": "check passed: pull request ready for the owner", "job": "box-x",
        "assignee": "miles", "owner": "miles", "closed_at": f"{int(now - 3600_000)}.0000.box",
        "opened_at": f"{int(now - 7200_000)}.0000.box"}])
    rows = autofix.escalations(Path("/nonexistent"), now_ms=now)
    assert any("box-x" in r for r in rows), (
        'a repair closed as done is treated as fixed although box-x has not verified since')
