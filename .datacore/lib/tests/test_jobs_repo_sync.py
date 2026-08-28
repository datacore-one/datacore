"""Tests for the repo staleness gate.

Covers two surfaces:
  - jobs.checks.check_repo_sync -- the low-level git query
  - job_verify._check_job -- the gate integrated into the job runner

The core invariant: when a job declares ``require_synced_repos`` and one of
those repos is behind its upstream, the artifact content checks are SKIPPED
and a "repo behind N commits" error is returned instead.  A host that cannot
sync must not emit confident wrong verdicts against stale on-disk content.

Root cause this pins (2026-08-20 through 2026-08-24): Winston's 0-personal
was 318 commits behind.  box-briefing read a 439-byte journal stub and
reported "content mismatch" for five consecutive days.  The real defect was
unsynced input, not the content.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import job_verify
from jobs.checks import check_repo_sync, run_check
from jobs.manifest import Artifact, Job

# ---------------------------------------------------------------------------
# check_repo_sync unit tests
# ---------------------------------------------------------------------------


def _mock_git(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    """Return a mock subprocess.CompletedProcess for git rev-list."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_repo_up_to_date_returns_empty(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _mock_git(stdout="0\n")
    )
    errors = check_repo_sync("~/Data/0-personal")
    assert errors == []


def test_repo_behind_returns_error_naming_count(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _mock_git(stdout="318\n")
    )
    errors = check_repo_sync(str(tmp_path / "0-personal"))
    assert len(errors) == 1
    assert "318" in errors[0]
    assert "behind" in errors[0]
    assert "cannot verify" in errors[0]


def test_non_git_directory_returns_empty(monkeypatch):
    """Not a git repo -> silent pass (not our job to police non-git paths)."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _mock_git(
            returncode=128,
            stderr="fatal: not a git repository (or any of the parent directories): .git",
        ),
    )
    errors = check_repo_sync("/tmp/not-a-repo")
    assert errors == []


def test_no_upstream_configured_returns_empty(monkeypatch):
    """No upstream -> silent pass (a local-only repo cannot be 'behind')."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _mock_git(
            returncode=128,
            stderr="fatal: no upstream configured for branch 'main'",
        ),
    )
    errors = check_repo_sync("~/Data/local-only")
    assert errors == []


def test_upstream_not_set_via_at_u_syntax_returns_empty(monkeypatch):
    """@{u} with no tracking branch -> silent pass."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _mock_git(
            returncode=128,
            stderr="fatal: no upstream configured for branch 'main' (@{u})",
        ),
    )
    errors = check_repo_sync("~/Data/local-only")
    assert errors == []


def test_other_git_failure_returns_error(monkeypatch, tmp_path):
    """An unexpected git error (not 'not a repo', not 'no upstream') surfaces."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _mock_git(
            returncode=128,
            stderr="error: object file .git/objects/ab/cd1234 is empty",
        ),
    )
    errors = check_repo_sync(str(tmp_path))
    assert len(errors) == 1
    assert "repo sync check failed" in errors[0]


def test_timeout_returns_error(monkeypatch, tmp_path):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)

    monkeypatch.setattr(subprocess, "run", _raise)
    errors = check_repo_sync(str(tmp_path))
    assert len(errors) == 1
    assert "repo sync check failed" in errors[0]


def test_oserror_returns_error(monkeypatch, tmp_path):
    def _raise(*a, **kw):
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    errors = check_repo_sync(str(tmp_path))
    assert len(errors) == 1
    assert "repo sync check failed" in errors[0]


def test_tilde_is_expanded_in_path_passed_to_git(monkeypatch, tmp_path):
    """~ is expanded before being passed to git -C, matching expand_path behavior."""
    calls = []

    def _capture(*args, **kwargs):
        calls.append(args[0])  # first positional is the command list
        return _mock_git(stdout="0\n")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", _capture)
    check_repo_sync("~/Data/0-personal")

    assert calls, "subprocess.run was never called"
    cmd = calls[0]
    assert "~" not in cmd[2], f"tilde was not expanded in git -C path: {cmd}"
    assert str(tmp_path) in cmd[2]


def test_behind_count_one_is_detected(monkeypatch):
    """Even a single missing commit is enough to block content checks."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _mock_git(stdout="1\n")
    )
    errors = check_repo_sync("~/Data/0-personal")
    assert len(errors) == 1
    assert "1 commit" in errors[0]


# ---------------------------------------------------------------------------
# _check_job staleness gate integration tests
# ---------------------------------------------------------------------------


def _job_with_repos(repos: list[str], artifact_path: str) -> Job:
    return Job(
        name="briefing",
        machine="box",
        schedule="0 4 * * *",
        cmd="true",
        artifacts=[Artifact(path=artifact_path, check="nonempty")],
        require_synced_repos=repos,
    )


def test_stale_repo_skips_artifact_checks_returns_only_sync_error(
    tmp_path, monkeypatch
):
    """The critical invariant: when a repo is behind, artifact content checks
    must NOT run.  Returning a wrong content-mismatch verdict from stale input
    is worse than returning no verdict -- it is a confident wrong answer.
    """
    # Artifact exists and passes content check on its own.
    artifact = tmp_path / "journal.md"
    artifact.write_text("stub content that would fail a regex check")

    job = _job_with_repos([str(tmp_path / "0-personal")], str(artifact))

    # Make check_repo_sync report "behind 318"
    monkeypatch.setattr(
        job_verify, "check_repo_sync",
        lambda path: [f"{path}: repo is 318 commit(s) behind upstream -- "
                      f"artifact content checks skipped (stale input, cannot verify)"],
    )
    # If run_check fires it would fail (stub content + nonempty is actually fine,
    # but the point is we verify it is NOT called at all).
    run_check_calls = []
    real_run_check = job_verify.run_check

    def _tracking_run_check(artifact, **kw):
        run_check_calls.append(artifact)
        return real_run_check(artifact, **kw)

    monkeypatch.setattr(job_verify, "run_check", _tracking_run_check)

    failures = job_verify._check_job(job)

    assert run_check_calls == [], "run_check must NOT be called when repo is behind"
    assert len(failures) == 1
    assert "318" in failures[0]
    assert "behind" in failures[0]


def test_synced_repo_runs_artifact_checks(tmp_path, monkeypatch):
    """When the repo is synced (0 behind), artifact checks proceed normally."""
    artifact = tmp_path / "journal.md"
    artifact.write_text("content")

    job = _job_with_repos([str(tmp_path / "0-personal")], str(artifact))

    monkeypatch.setattr(job_verify, "check_repo_sync", lambda path: [])

    failures = job_verify._check_job(job)

    assert failures == []


def test_no_require_synced_repos_runs_artifact_checks_directly(tmp_path):
    """A job with no require_synced_repos skips the git gate entirely."""
    artifact = tmp_path / "backup.log"
    artifact.write_text("ok")

    job = Job(
        name="backup",
        machine="box",
        schedule="0 3 * * *",
        cmd="true",
        artifacts=[Artifact(path=str(artifact), check="nonempty")],
        require_synced_repos=[],
    )

    failures = job_verify._check_job(job)

    assert failures == []


def test_multiple_repos_one_behind_skips_artifacts(tmp_path, monkeypatch):
    """When any listed repo is behind, all artifact checks are skipped."""
    artifact = tmp_path / "journal.md"
    artifact.write_text("content")

    repo_a = str(tmp_path / "repo-a")
    repo_b = str(tmp_path / "repo-b")

    def _sync_check(path: str) -> list[str]:
        if path == repo_b:
            return [f"{path}: repo is 42 commit(s) behind upstream -- "
                    f"artifact content checks skipped (stale input, cannot verify)"]
        return []

    monkeypatch.setattr(job_verify, "check_repo_sync", _sync_check)

    run_check_calls = []
    real_run_check = job_verify.run_check

    def _tracking(artifact, **kw):
        run_check_calls.append(artifact)
        return real_run_check(artifact, **kw)

    monkeypatch.setattr(job_verify, "run_check", _tracking)

    job = Job(
        name="multi-repo-job",
        machine="box",
        schedule="0 4 * * *",
        cmd="true",
        artifacts=[Artifact(path=str(artifact), check="nonempty")],
        require_synced_repos=[repo_a, repo_b],
    )

    failures = job_verify._check_job(job)

    assert run_check_calls == []
    assert len(failures) == 1
    assert "42" in failures[0]


def test_stale_repo_failure_is_attested_and_alerts(tmp_path, monkeypatch, capsys):
    """End-to-end: a stale repo causes job.verify to exit 1, emit an attest
    event with ok=False, and dispatch an alert -- identical to any other job
    failure from the runner's perspective."""
    artifact = tmp_path / "journal.md"
    artifact.write_text("stub")

    manifest_data = {
        "version": 1,
        "jobs": [
            {
                "name": "box-briefing",
                "machine": "box",
                "schedule": "0 4 * * *",
                "cmd": "true",
                "require_synced_repos": [str(tmp_path / "0-personal")],
                "artifacts": [{"path": str(artifact), "check": "nonempty"}],
                "on_fail": "log",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest_data))
    space = tmp_path / "space"
    space.mkdir()

    monkeypatch.setattr(
        job_verify, "check_repo_sync",
        lambda path: [f"{path}: repo is 318 commit(s) behind upstream -- "
                      f"artifact content checks skipped (stale input, cannot verify)"],
    )
    monkeypatch.setenv("DATACORE_ACTOR", "test")

    try:
        job_verify.main(
            ["--machine", "box", "--manifest", str(manifest_path), "--space", str(space)]
        )
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0

    out = capsys.readouterr()
    assert code == 1
    assert "box-briefing" in out.err
    assert "318" in out.err
    assert "behind" in out.err
    # Content check error must NOT appear -- only the sync error
    assert "nonempty" not in out.err
