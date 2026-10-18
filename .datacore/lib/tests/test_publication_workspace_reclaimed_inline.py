"""A publication removes its own checkout once that checkout is published.

2026-10-18: the overnight server's disk filled. Every merge that
git_integration.integrate() publishes after a rejected push made a full
checkout (~440MB in 5-plur), retired it, and nothing reclaimed it. 40 busy
publishes in two days added 17GB. publication_workspace_gc.py held the proof
but nothing ran it. So the writer reclaims its own copy, under the same proof,
right after the push lands; a copy that fails the proof stays.
"""
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import publication_workspace_gc as gc  # noqa: E402
from git_integration import integrate  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _clone(origin, path):
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t"),
                       ("core.hooksPath", str(path / ".git" / "hooks"))):
        git(path, "config", key, value)
    return path


@pytest.fixture
def diverged(tmp_path):
    """A local commit whose push is rejected because another writer pushed first."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    seed = _clone(origin, tmp_path / "seed")
    (seed / "seed.md").write_text("seed\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "seed")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
    work = _clone(origin, tmp_path / "1-fixture")
    git(work, "remote", "set-head", "origin", "main")
    (seed / "other.md").write_text("another writer\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "other writer")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
    (work / "report.md").write_text("tonight's report\n")
    git(work, "add", "-A"); git(work, "commit", "-qm", "report")
    return work, origin


def test_a_published_integration_leaves_no_checkout_behind(diverged):
    work, origin = diverged
    source = git(work, "rev-parse", "HEAD")

    integrate(work, source, "refs/heads/main")

    published = git(origin, "rev-parse", "refs/heads/main")
    assert git(origin, "merge-base", "--is-ancestor", source, published) == ""
    assert gc.workspace_worktrees(work) == [], "the full merge checkout is a copy of what was just pushed"


def test_a_copy_that_fails_the_proof_is_kept(diverged, monkeypatch):
    work, _ = diverged
    monkeypatch.setattr(gc, "files_are_index", lambda worktree: False)

    integrate(work, git(work, "rev-parse", "HEAD"), "refs/heads/main")

    assert len(gc.workspace_worktrees(work)) == 1, "an unproven copy may be the only copy"


def test_cleanup_trouble_never_fails_a_publication_that_landed(diverged, monkeypatch):
    work, origin = diverged
    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(gc, "reclaim_worktree", broken)

    integrate(work, git(work, "rev-parse", "HEAD"), "refs/heads/main")

    assert git(origin, "log", "--format=%s", "-1", "refs/heads/main").startswith("Merge")


def test_a_pushed_knowledge_commit_leaves_no_checkout_behind(tmp_path):
    """commit_to_branch off the current branch makes a reservation and a candidate
    worktree per publication; once pushed, both are copies."""
    from knowledge_commit import commit_to_branch
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    work = _clone(origin, tmp_path / "1-fixture")
    (work / "seed.md").write_text("seed\n")
    git(work, "add", "-A"); git(work, "commit", "-qm", "seed")
    git(work, "branch", "-M", "main")
    git(work, "push", "-q", "-u", "origin", "main")
    git(work, "remote", "set-head", "origin", "main")
    git(work, "switch", "-qc", "elsewhere")
    (work / "note.md").write_text("knowledge\n")

    sha = commit_to_branch(work, "main", ["note.md"], "note", push=True)

    assert sha and git(origin, "rev-parse", "refs/heads/main") == sha
    assert gc.workspace_worktrees(work) == [], "both publication checkouts are copies of what was pushed"
