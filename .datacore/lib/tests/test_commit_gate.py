#!/usr/bin/env python3
"""The commit-decision gate, verified by causing what it prevents (DIP-0046 E3).

The behaviour under test is `git add -A` in an unattended run: a task that
edits one report also commits whatever else is in the tree, under the task's
message. The decisive test is therefore not "does the gate return the right
object" but "after a run, is the unrelated file still uncommitted".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import commit_gate  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch):
    r = tmp_path / "r"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    git(r, "config", "core.hooksPath", str(r / ".git" / "hooks"))
    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "seed")
    monkeypatch.setattr(commit_gate, "PENDING", tmp_path / "decisions")
    return r


def test_run_output_is_allowed(repo: Path):
    (repo / "report.md").write_text("the task's own output\n")
    d = commit_gate.decide(repo, ["report.md"], task_id="t1")
    assert d.allowed == ["report.md"]
    assert d.clean


def test_unrelated_work_is_withheld(repo: Path):
    (repo / "report.md").write_text("output\n")
    (repo / "half-finished.py").write_text("someone was editing this\n")
    d = commit_gate.decide(repo, ["report.md"], task_id="t2")
    assert d.allowed == ["report.md"]
    assert d.withheld == ["half-finished.py"]
    assert not d.clean


def test_undeclared_output_is_recorded_not_blocked(repo: Path):
    """produced=None commits and RECORDS. Blocking it stopped a live batch.

    Withholding everything here looked principled and broke production: every
    caller in nightshift's run.py passes no file list, so eight tasks ran and
    committed nothing — their outputs, ledger events and org updates all
    withheld. The strict guarantee applies where a caller DECLARES outputs;
    otherwise the gate is an audit trail.
    """
    (repo / "mystery.txt").write_text("x\n")
    d = commit_gate.decide(repo, None, task_id="t3")
    assert d.allowed == ["mystery.txt"]
    assert d.withheld == []
    assert d.record and d.record.exists()


def test_the_actual_failure_unrelated_file_is_not_committed(repo: Path):
    """The whole point: after the gated commit, the stranger is still dirty."""
    (repo / "report.md").write_text("output\n")
    (repo / "secret-scratch.txt").write_text("nobody reviewed this\n")

    d = commit_gate.decide(repo, ["report.md"], task_id="t4")
    for f in d.allowed:
        git(repo, "add", f)
    git(repo, "commit", "-qm", "nightshift: task t4")

    committed = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["report.md"]
    assert "secret-scratch.txt" in git(repo, "status", "--porcelain")


def test_decision_is_recorded_with_what_and_who(repo: Path):
    (repo / "stray.txt").write_text("x\n")
    d = commit_gate.decide(repo, [], task_id="task-99", actor="nightshift")
    assert d.record and d.record.exists()
    rows = commit_gate.pending()
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-99"
    assert rows[0]["withheld"] == ["stray.txt"]


def test_nothing_dirty_records_nothing(repo: Path):
    d = commit_gate.decide(repo, ["report.md"], task_id="t5")
    assert d.clean and d.record is None
    assert commit_gate.pending() == []


def test_untracked_counts_as_dirty(repo: Path):
    """An untracked file is exactly what `add -A` would have swept in."""
    (repo / "brand new.txt").write_text("x\n")   # space: porcelain quotes it
    assert "brand new.txt" in commit_gate.dirty_paths(repo)


def test_gate_defaults_on(monkeypatch):
    monkeypatch.delenv("DATACORE_COMMIT_GATE", raising=False)
    assert commit_gate.enabled()
    monkeypatch.setenv("DATACORE_COMMIT_GATE", "0")
    assert not commit_gate.enabled()


def test_new_untracked_directory_is_listed_per_file(repo: Path):
    """git collapses a new untracked dir; a declared path inside it must match.

    Plain `--porcelain` reports "0-inbox/" rather than the file within, so a
    task whose output lands in a brand-new directory declared a path matching
    nothing and had its OWN work withheld. -uall lists the files.
    """
    (repo / "0-inbox").mkdir()
    (repo / "0-inbox" / "out.md").write_text("task output\n")
    (repo / "stranger.txt").write_text("someone else\n")

    d = commit_gate.decide(repo, ["0-inbox/out.md"], task_id="t9")

    assert "0-inbox/out.md" in d.allowed
    assert d.withheld == ["stranger.txt"]
