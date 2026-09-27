"""A user's fork is public, so pushes to it get the same policy chain as upstream.

The pre-push hook protected a fixed list of repos: datacore-one/datacore was
scanned and alice/datacore — a public fork of it, created by `datacore init` —
printed "not a protected public repo — policy check skipped". The fork is where
a user's pushes actually go, so the scan never ran for anyone but the operator.

The persona file is the concrete case: init writes the Chief of Staff's name
and the user's free-text "how I want to be worked with" notes to
.datacore/personas/winston.md, which was neither ignored nor tracked.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / ".datacore" / "githooks" / "pre-push"
ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
         "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(tmp_path: Path, upstream: str | None) -> Path:
    repo = tmp_path / "Data"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", "https://github.com/alice/datacore.git")
    if upstream:
        _git(repo, "remote", "add", "upstream", upstream)
    persona = repo / ".datacore" / "personas" / "winston.md"
    persona.parent.mkdir(parents=True)
    persona.write_text("displayName: Babbage\nHow they want to be worked with: I trade in the mornings.\n")
    _git(repo, "add", "-f", ".")
    _git(repo, "commit", "-q", "-m", "persona")
    return repo


def _push(repo: Path) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = {**os.environ, "DATA_DIR": str(ROOT)}
    env.pop("SKIP_PRE_PUSH", None)
    return subprocess.run(
        ["bash", str(HOOK), "origin", "https://github.com/alice/datacore.git"],
        cwd=repo, input=f"refs/heads/main {sha} refs/heads/main {ZERO}\n",
        capture_output=True, text=True, env=env, timeout=120,
    )


@pytest.mark.parametrize("upstream", [
    "https://github.com/datacore-one/datacore.git",
    "git@github.com:datacore-one/datacore.git",
])
def test_push_to_a_fork_of_a_protected_repo_is_scanned(tmp_path, upstream):
    r = _push(_repo(tmp_path, upstream))
    assert "policy check skipped" not in r.stderr, r.stderr
    assert "PROTECTED" in r.stderr, r.stderr
    # ...and the scan does its job: the persona is not an allowed new file.
    assert r.returncode != 0, r.stderr
    assert ".datacore/personas/winston.md" in r.stderr, r.stderr


def test_unrelated_repo_is_still_skipped(tmp_path):
    r = _push(_repo(tmp_path, None))
    assert "policy check skipped" in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr


def test_persona_is_gitignored():
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", ".datacore/personas/winston.md"])
    assert r.returncode == 0, ".datacore/personas/ must be ignored: it holds the user's own words"


def _fake_gh(tmp_path: Path, visibility: str | None) -> dict:
    """A `gh` on PATH that answers `repo view ... visibility` (None = gh fails)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    body = f'echo "{visibility}"' if visibility else "exit 1"
    gh.write_text(f"#!/bin/sh\n{body}\n")
    gh.chmod(0o755)
    return {"PATH": f"{bindir}:{os.environ['PATH']}"}


def _push_with(repo: Path, extra_env: dict) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = {**os.environ, "DATA_DIR": str(ROOT), **extra_env}
    env.pop("SKIP_PRE_PUSH", None)
    return subprocess.run(
        ["bash", str(HOOK), "origin", "https://github.com/alice/datacore.git"],
        cwd=repo, input=f"refs/heads/main {sha} refs/heads/main {ZERO}\n",
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_a_private_repo_with_a_public_upstream_remote_is_not_a_public_fork(tmp_path):
    """A private space repo keeps the public template as its `upstream` remote.
    GitHub says the push target is PRIVATE, so it is not a public fork and its
    journals and task files must not be blocked (2-datacore, 2026-09-27)."""
    repo = _repo(tmp_path, "https://github.com/datacore-one/datacore.git")
    r = _push_with(repo, _fake_gh(tmp_path, "PRIVATE"))
    assert "treating as PROTECTED" not in r.stderr, r.stderr


def test_when_visibility_cannot_be_read_the_fork_is_still_scanned(tmp_path):
    """Fail closed: no answer from GitHub keeps the fork protected."""
    repo = _repo(tmp_path, "https://github.com/datacore-one/datacore.git")
    r = _push_with(repo, _fake_gh(tmp_path, None))
    assert "treating as PROTECTED" in r.stderr, r.stderr
