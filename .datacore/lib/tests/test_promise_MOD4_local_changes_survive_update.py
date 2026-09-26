"""MOD-4: My local changes to a module survive updates from the shared source.

Kind: deterministic. A module is a git checkout under .datacore/modules/<m>; the
update from the shared source is git_fleet_sync.sync_repo(..., pull=True) -- the
fleet sync every host runs. Here the "shared source" is a tmp bare repository,
another contributor pushes to it, and the local checkout carries three kinds of
local change: a DIP-0002 private overlay (CLAUDE.local.md), an uncommitted edit
to a tracked file, and a local commit.

Seeded failure: the update resets or checks out over the local edit (reset --hard,
checkout --theirs, a re-clone), drops the local commit, or deletes the overlay;
or a clash with an upstream edit of the same line is resolved by discarding mine.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    tmp = tmp_path.resolve()
    home = tmp / "home"
    home.mkdir()
    for k, v in {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "eval",
                 "GIT_AUTHOR_EMAIL": "eval@example.test", "GIT_COMMITTER_NAME": "eval",
                 "GIT_COMMITTER_EMAIL": "eval@example.test", "DATACORE_STATE": str(tmp / "state")}.items():
        monkeypatch.setenv(k, v)
    shared = tmp / "shared.git"
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(shared))
    seed = tmp / "seed"
    _git(tmp, "clone", "-q", str(shared), str(seed))
    (seed / "module.yaml").write_text("name: evalmod\nversion: 1.0.0\n")
    (seed / "prompt.md").write_text("line one\nline two\nline three\n")
    (seed / ".gitignore").write_text("*.local.md\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "v1")
    _git(seed, "push", "-q", "origin", "main")
    local = tmp / "Data" / ".datacore" / "modules" / "evalmod"
    local.parent.mkdir(parents=True)
    _git(tmp, "clone", "-q", str(shared), str(local))
    _git(local, "remote", "set-head", "origin", "main")
    return seed, local


def _sync(local):
    import git_fleet_sync as G
    return G.sync_repo(local, execute=True, pull=True)


def test_local_overlay_edit_and_commit_survive_an_upstream_update(fleet):
    seed, local = fleet
    (local / "CLAUDE.local.md").write_text("my private overlay\n")
    (local / "notes.md").write_text("my local commit\n")
    _git(local, "add", "notes.md")
    _git(local, "commit", "-q", "-m", "local")
    (local / "prompt.md").write_text("line one\nline two\nline three\nmy local line\n")

    (seed / "module.yaml").write_text("name: evalmod\nversion: 1.1.0\n")
    _git(seed, "commit", "-q", "-am", "v1.1")
    _git(seed, "push", "-q", "origin", "main")

    result = _sync(local)

    assert "version: 1.1.0" in (local / "module.yaml").read_text(), f"the update did not arrive: {result}"
    assert (local / "CLAUDE.local.md").read_text() == "my private overlay\n", "the private overlay was lost"
    assert "my local line" in (local / "prompt.md").read_text(), f"the local edit was lost: {result}"
    assert (local / "notes.md").exists() and "local" in _git(local, "log", "--format=%s"), "the local commit was lost"


def test_a_clash_with_an_upstream_edit_keeps_my_version(fleet):
    seed, local = fleet
    mine = "line one\nMY VERSION of line two\nline three\n"
    (local / "prompt.md").write_text(mine)

    (seed / "prompt.md").write_text("line one\nTHEIR version of line two\nline three\n")
    _git(seed, "commit", "-q", "-am", "upstream edit")
    _git(seed, "push", "-q", "origin", "main")

    result = _sync(local)

    text = (local / "prompt.md").read_text()
    assert "MY VERSION of line two" in text, f"my edit was discarded by the update: {result}\n{text}"
    assert "<<<<<<<" not in text, "conflict markers were left in the module"
