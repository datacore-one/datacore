"""Tests for git_branch_guard — the check that would have caught winston.

Winston served as Chief of Staff from a feature branch for four days while
`cos_sync.sh` logged "not fast-forwardable" 1,326 times and alerted nobody.
The guard exists to turn that into a reported, and where provably safe a
repaired, condition.

Real throwaway git repos: the whole subject is git's own branch/ancestry
state, and a mock would only assert that the mock was called.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import git_branch_guard as guard  # noqa: E402


def _git(args, cwd):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r


@pytest.fixture
def repo(tmp_path):
    """A clone of a bare origin, on main, with origin/HEAD set like a real one."""
    bare = tmp_path / "origin.git"
    _git(["init", "--bare", "-q", "-b", "main", str(bare)], tmp_path)

    work = tmp_path / "0-space"
    _git(["init", "-q", "-b", "main", str(work)], tmp_path)
    _git(["config", "user.email", "t@example.com"], work)
    _git(["config", "user.name", "T"], work)
    (work / "f.txt").write_text("seed\n")
    _git(["add", "-A"], work)
    _git(["commit", "-q", "--no-verify", "-m", "init"], work)
    _git(["remote", "add", "origin", str(bare)], work)
    _git(["push", "-q", "-u", "origin", "main"], work)
    _git(["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], work)
    return work


def test_on_main_is_ok(repo):
    assert guard.inspect(repo)["state"] == "OK"


def test_feature_branch_without_unique_commits_is_repaired(repo):
    _git(["checkout", "-q", "-b", "fix/stray"], repo)

    info = guard.inspect(repo)
    assert info["state"] == "OFF_BRANCH"
    assert info["unique"] == "0"

    info = guard.repair(repo, info)
    assert info["state"] == "REPAIRED"
    assert guard.inspect(repo)["state"] == "OK"


def test_branch_carrying_work_is_never_moved(repo):
    """The winston case exactly: a branch holding a real commit.

    Auto-returning here would strand the commit on a branch nobody is
    looking at — the same loss the guard exists to surface.
    """
    _git(["checkout", "-q", "-b", "fix/real-work"], repo)
    (repo / "fix.txt").write_text("a real fix\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "--no-verify", "-m", "ops: a real fix"], repo)

    info = guard.repair(repo, guard.inspect(repo))

    assert info["state"] == "STUCK"
    assert "1 commit(s)" in info["detail"]
    assert guard.inspect(repo)["branch"] == "fix/real-work", "must not have moved"


def test_dirty_worktree_is_never_moved(repo):
    _git(["checkout", "-q", "-b", "fix/dirty"], repo)
    (repo / "f.txt").write_text("uncommitted edit\n")

    info = guard.repair(repo, guard.inspect(repo))

    assert info["state"] == "STUCK"
    assert "uncommitted" in info["detail"]


def test_untracked_files_do_not_block_repair(repo):
    """Untracked files survive a branch switch, so they are not "dirty".

    Counting them made the guard report STUCK forever on exactly the hosts
    it exists to repair — winston permanently carries .datacore/keys/, scp
    backups and host-local scripts, and the first drill refused to move a
    branch whose only "dirt" was files git never tracked.
    """
    _git(["checkout", "-q", "-b", "fix/with-junk"], repo)
    (repo / "scratch.tmp").write_text("not tracked\n")
    (repo / "keys").mkdir()
    (repo / "keys" / "local.yaml").write_text("host-local\n")

    info = guard.repair(repo, guard.inspect(repo))

    assert info["state"] == "REPAIRED"
    assert (repo / "scratch.tmp").exists(), "untracked file must survive"


def test_detached_head_is_reported_not_touched(repo):
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    _git(["checkout", "-q", sha], repo)

    info = guard.inspect(repo)

    assert info["state"] == "DETACHED"


def test_expected_branch_read_from_origin_not_assumed(tmp_path):
    """A master-default repo must not be 'repaired' onto a nonexistent main."""
    bare = tmp_path / "o.git"
    _git(["init", "--bare", "-q", "-b", "master", str(bare)], tmp_path)
    work = tmp_path / "1-master-space"
    _git(["init", "-q", "-b", "master", str(work)], tmp_path)
    _git(["config", "user.email", "t@example.com"], work)
    _git(["config", "user.name", "T"], work)
    (work / "f.txt").write_text("x\n")
    _git(["add", "-A"], work)
    _git(["commit", "-q", "--no-verify", "-m", "init"], work)
    _git(["remote", "add", "origin", str(bare)], work)
    _git(["push", "-q", "-u", "origin", "master"], work)
    _git(["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"], work)

    assert guard.expected_branch(work) == "master"
    assert guard.inspect(work)["state"] == "OK"


def test_unique_commits_measured_against_remote_not_stale_local(repo):
    """Compare to origin/main, not a local main that may lag.

    If the commit is already on origin, the branch carries nothing unique
    and refusing to repair would be a false positive that keeps a host
    off-branch — and therefore undeployed — for no reason.
    """
    _git(["checkout", "-q", "-b", "fix/pushed"], repo)
    (repo / "g.txt").write_text("shipped\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "--no-verify", "-m", "shipped"], repo)
    _git(["push", "-q", "origin", "HEAD:main"], repo)
    _git(["fetch", "-q", "origin"], repo)

    info = guard.inspect(repo)

    assert info["base"] == "origin/main"
    assert info["unique"] == "0"
    assert guard.repair(repo, info)["state"] == "REPAIRED"
