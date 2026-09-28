"""The fleet sync never publishes commits it did not make onto a code repo's default branch.

2026-09-27 20:31Z an overnight task committed 93f3879 onto the nightshift
host's local `main` of the root Datacore repository (public). The next morning
the host's sweep (git_fleet_sync.py --execute --pull, datacore-fleet-sync.timer)
merged origin into that main twice and found the tree clean, so it pushed
nothing -- by luck. Had one agent-written file been left uncommitted, the sweep
would have committed it and pushed `HEAD` to origin/main: its own commit AND
the agent's, unreviewed, onto a public default branch.

The sweep lands UNCOMMITTED work; commits already on the branch are someone's
decision, and on a code repository that decision is a pull request the owner
merges. So:
  * code repo, local main ahead of origin with a commit the sweep did not make:
    nothing is pushed (not the agent's commit, not the sweep's own), the repo
    and the commit are named, and the run fails so the alert fires -- also when
    the tree is clean and there is nothing to commit;
  * a knowledge space keeps its existing path (its default branch IS where
    knowledge lands, DIP-0046);
  * a code repo whose only unpushed commit is the sweep's own still pushes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import git_fleet_sync as fs  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "scheduled_run_active", lambda: False)
    monkeypatch.setattr(fs, "review_gate", lambda repo, branch: "")

    def _make(remote_name: str):
        origin = tmp_path / f"{remote_name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
        repo = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "T")):
            _git(repo, "config", k, v)
        _git(repo, "remote", "add", "origin", str(origin))
        (repo / "a.txt").write_text("base\n")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "push", "-q", "-u", "origin", "main")
        _git(repo, "remote", "set-head", "origin", "main")
        return repo, origin
    return _make


def _agent_commit(repo: Path) -> str:
    (repo / "sprint_validate.py").write_text("# agent change\n")
    _git(repo, "add", "sprint_validate.py")
    _git(repo, "commit", "-qm", "sprint schema validation: close three failing-open holes")
    return _git(repo, "rev-parse", "HEAD")


def test_code_repo_with_an_agent_commit_on_main_is_not_pushed(make):
    repo, origin = make("datacore")                      # the root: code
    before = _git(origin, "rev-parse", "main")
    sha = _agent_commit(repo)
    (repo / "notes.md").write_text("left uncommitted by an agent\n")
    r = fs.sync_repo(repo, execute=True)
    assert _git(origin, "rev-parse", "main") == before, "the sweep published a commit it did not make"
    assert "PUSH REFUSED" in r["status"], r["status"]
    assert any(sha[:12] in f for f in r.get("foreign", [])), r


def test_clean_code_repo_ahead_of_origin_is_reported(make):
    repo, origin = make("datacore")
    sha = _agent_commit(repo)
    r = fs.sync_repo(repo, execute=True)
    assert any(sha[:12] in f for f in r.get("foreign", [])), r
    assert r["status"] != "clean"


def test_main_fails_and_names_the_repo_and_commit(make, capsys, monkeypatch, tmp_path):
    repo, origin = make("datacore")
    sha = _agent_commit(repo)
    monkeypatch.setattr(fs, "find_repos", lambda root: [repo])
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(tmp_path), "--execute"])
    assert fs.main() == 1
    out = capsys.readouterr().out
    assert "work" in out and sha[:12] in out


def test_code_repo_pushes_when_the_only_new_commit_is_the_sweeps_own(make):
    repo, origin = make("datacore")
    (repo / "notes.md").write_text("left uncommitted by an agent\n")
    r = fs.sync_repo(repo, execute=True)
    assert r["status"].startswith("PUSHED"), r["status"]
    assert _git(origin, "rev-parse", "main") == _git(repo, "rev-parse", "HEAD")


def test_knowledge_space_keeps_its_direct_path(make):
    repo, origin = make("datacore-space")                # registered knowledge (2-datacore)
    _agent_commit(repo)
    (repo / "notes.md").write_text("journal\n")
    r = fs.sync_repo(repo, execute=True)
    assert r["status"].startswith("PUSHED"), r["status"]
