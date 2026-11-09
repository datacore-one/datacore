"""fix_check --stage merged: a pull request advances a repair, the job verifying again finishes it; bounded by what it touched."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
FIX_CHECK = LIB / "jobs" / "fix_check.py"


def _fake_gh(tmp_path, prs, files):
    b = tmp_path / "bin"; b.mkdir()
    (b / "prs.json").write_text(json.dumps(prs)); (b / "files.json").write_text(json.dumps({"files": [{"path": p} for p in files]}))
    (b / "gh").write_text(f"#!/bin/bash\ncase \"$2\" in list) cat {b}/prs.json;; view) cat {b}/files.json;; *) exit 9;; esac\n")
    (b / "gh").chmod(0o755)
    return b


def _run(tmp_path, bindir, *extra):
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    return subprocess.run([sys.executable, str(FIX_CHECK), "--job", "box-x", "--machine", "box",
                          "--contract-sha", "abc", "--stage", "merged", *extra],
                         capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60)


def test_a_merged_pr_alone_is_not_a_repair(tmp_path):
    """NS-10 (owner, 2026-09-26) supersedes the 2026-09-25 reading: merged, and the job
    has not verified since, is not fixed."""
    gh = _fake_gh(tmp_path, [{"number": 7, "title": "fix box-x (autofix-box-x-20260922)", "body": "", "state": "MERGED",
                              "mergedAt": "2026-09-22T10:00:00Z", "url": "https://x/pull/7"}], ["lib/producer.py"])
    r = _run(tmp_path, gh, "--item", "autofix-box-x-20260922", "--repo", "o/r")
    assert r.returncode == 1 and "https://x/pull/7" in r.stderr and "not yet" in r.stderr


def test_a_merged_pr_after_which_the_job_verifies_is_repaired(tmp_path, monkeypatch):
    ev = tmp_path / "root" / "2-datacore" / ".datacore" / "events"; ev.mkdir(parents=True)
    (ev / "winston.jsonl").write_text(json.dumps({"actor": "winston", "hlc": "1800000000000.0000.winston",
        "payload": {"metric": "job.verify", "job": "box-x", "ok": True}}) + "\n")
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path / "root"))
    gh = _fake_gh(tmp_path, [{"number": 7, "title": "fix box-x (autofix-box-x-20260922)", "body": "", "state": "MERGED",
                              "mergedAt": "2026-09-22T10:00:00Z", "url": "https://x/pull/7"}], ["lib/producer.py"])
    r = _run(tmp_path, gh, "--item", "autofix-box-x-20260922", "--repo", "o/r")
    assert r.returncode == 0 and "repaired" in r.stdout


def test_an_open_pr_naming_the_item_is_waiting_for_the_owner_not_done(tmp_path):
    """Owner, 2026-09-25: an agent opens the pull request and stops. NS-10: that is
    "waiting for you", never "fixed"."""
    gh = _fake_gh(tmp_path, [{"number": 9, "title": "fix box-x (autofix-box-x-20260922)", "body": "", "state": "OPEN",
                              "mergedAt": None, "url": "https://x/pull/9"}], ["lib/producer.py"])
    r = _run(tmp_path, gh, "--item", "autofix-box-x-20260922", "--repo", "o/r")
    # NS-9: its own exit (WAITING_ON_OWNER = 3), so ledger_claim does not count it
    # as a failed attempt and dead-letter a finished repair.
    assert r.returncode == 3 and "waiting for you" in r.stderr


def test_a_closed_or_unrelated_pr_is_not_yet(tmp_path):
    gh = _fake_gh(tmp_path, [{"number": 7, "title": "fix box-x (autofix-box-x-20260922)", "body": "", "state": "CLOSED", "mergedAt": None, "url": "u"},
                             {"number": 8, "title": "unrelated", "body": "", "mergedAt": "2026-09-22T10:00:00Z", "url": "u8"}], [])
    r = _run(tmp_path, gh, "--item", "autofix-box-x-20260922", "--repo", "o/r")
    assert r.returncode == 1 and "not yet" in r.stderr


def test_a_merge_that_touched_the_manifest_is_refused(tmp_path):
    """The boundary, at the stage where the artifacts are out of reach."""
    gh = _fake_gh(tmp_path, [{"number": 7, "title": "autofix-box-x-20260922", "body": "", "state": "OPEN", "mergedAt": None, "url": "u"}],
                  ["lib/producer.py", ".datacore/lib/jobs/manifest.yaml"])
    r = _run(tmp_path, gh, "--item", "autofix-box-x-20260922", "--repo", "o/r")
    assert r.returncode == 1 and "REFUSED" in r.stderr and "manifest" in r.stderr


def test_the_stage_needs_an_item_and_a_repo(tmp_path):
    r = _run(tmp_path, _fake_gh(tmp_path, [], []))
    assert r.returncode == 2
