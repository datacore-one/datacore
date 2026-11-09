"""Finished agent task copies are removed once nothing in them is only there.

2026-10-18: the overnight server held 15GB of task copies under
~/.datacore/worktrees, some idle 18 days, none ever removed. A copy goes only
when all three hold: no unsaved work (no modified or untracked file), its commit
is on a remote, and it has been idle for the threshold. Removal is
`git worktree remove` without --force, so git re-checks and refuses rather than
deletes. Ignored build output (node_modules) is regenerable and goes with it.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import agent_workspace_gc as gc  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def setup(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    src = tmp_path / "src"
    subprocess.run(["git", "clone", "-q", str(origin), str(src)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(src / ".git" / "hooks"))):
        git(src, "config", key, value)
    (src / ".gitignore").write_text("node_modules/\n")
    (src / "a.md").write_text("a\n")
    git(src, "add", "-A"); git(src, "commit", "-qm", "seed"); git(src, "branch", "-M", "main")
    git(src, "push", "-q", "-u", "origin", "main")
    root = tmp_path / "worktrees"
    root.mkdir()
    return src, root


def _task(src, root, name, *, push=True, dirty=False, idle_days=10):
    path = root / f"src-x/{name}"
    git(src, "worktree", "add", "-q", "-b", f"agent/{name}", str(path), "main")
    (path / "out.md").write_text(name + "\n")
    git(path, "add", "-A"); git(path, "commit", "-qm", name)
    if push:
        git(path, "push", "-q", "origin", f"agent/{name}")
    (path / "node_modules").mkdir()
    (path / "node_modules" / "dep.js").write_text("x")
    if dirty:
        (path / "notes.md").write_text("unsaved\n")
    old = time.time() - idle_days * 86400
    gitdir = Path(git(path, "rev-parse", "--absolute-git-dir"))
    for f in (gitdir / "index", gitdir / "HEAD", path):
        os.utime(f, (old, old))
    return path


def test_a_finished_pushed_idle_copy_is_removed_and_its_branch_kept(setup):
    src, root = setup
    path = _task(src, root, "done")
    gc.sweep(root, idle_days=3, apply=True)
    assert not path.exists()
    assert git(src, "rev-parse", "--verify", "agent/done"), "the branch and its commit stay"


@pytest.mark.parametrize("kw,why", [
    ({"push": False}, "unpushed commit may be the only copy"),
    ({"dirty": True}, "untracked file is unsaved work"),
    ({"idle_days": 0}, "a copy in use tonight is not finished"),
])
def test_a_copy_failing_any_condition_stays(setup, kw, why):
    src, root = setup
    path = _task(src, root, "keep", **kw)
    gc.sweep(root, idle_days=3, apply=True)
    assert path.exists(), why


def test_report_mode_changes_nothing(setup):
    src, root = setup
    path = _task(src, root, "done")
    assert gc.sweep(root, idle_days=3, apply=False)[0] == 1
    assert path.exists()
