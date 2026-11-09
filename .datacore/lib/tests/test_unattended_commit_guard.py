"""An overnight task never commits onto a default branch of a code repository.

2026-09-27 20:31Z: the overnight task "Fix sprint schema validation" ran with
its working directory at ~/Data on `main` of the root Datacore repository (a
PUBLIC code repo), and the executing agent committed its change 93f3879
straight onto the host's local `main`. Nothing refused it: every git guard
looked at pushes, none at commits. Only luck kept it from being published.

The rule (owner, standing): overnight code work is delivered as a branch and a
pull request; agents never merge. These tests drive the REAL git hooks
(.datacore/githooks via core.hooksPath) on a tmp repository:

  * under NIGHTSHIFT_RUN=1 (what the executor sets for the agent and every
    process it starts), a commit on main/master/develop/development of a code
    repository is refused, and the refusal says how to do it right;
  * the same commit on the task branch (agent/<task-id>) goes through;
  * a human commit (no NIGHTSHIFT_RUN) on main is untouched;
  * a registered knowledge space keeps its direct path (DIP-0046: knowledge
    integrates on the default branch);
  * the task workspace the executor tells the agent to open
    (agent_workspace.py ensure) is on the task branch, so committing there works.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
GITHOOKS = ROOT / ".datacore" / "githooks"
sys.path.insert(0, str(LIB))


def _git(cwd, *args, env=None, check=True):
    base = {k: v for k, v in os.environ.items() if k != "NIGHTSHIFT_RUN"}
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120,
                       env={**base, **(env or {})})
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p


def _repo(tmp_path, remote_name, branch="main"):
    remote = tmp_path / f"{remote_name}.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", branch, str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "-b", branch, str(work))
    for k, v in (("user.name", "eval"), ("user.email", "eval@example.invalid"),
                 ("core.hooksPath", str(GITHOOKS))):
        _git(work, "config", k, v)
    _git(work, "remote", "add", "origin", str(remote))
    (work / "a.txt").write_text("base\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "base")
    return work


UNATTENDED = {"NIGHTSHIFT_RUN": "1"}


def _commit(work, env=None, name="change.txt"):
    (work / name).write_text("agent change\n")
    _git(work, "add", name, env=env)
    return _git(work, "commit", "-q", "-m", "agent change", env=env, check=False)


@pytest.mark.parametrize("branch", ["main", "master", "develop", "development"])
def test_overnight_commit_onto_default_branch_of_code_repo_is_refused(tmp_path, branch):
    work = _repo(tmp_path, "datacore", branch=branch)       # origin name -> the root: code
    head = _git(work, "rev-parse", "HEAD").stdout
    p = _commit(work, env=UNATTENDED)
    assert p.returncode != 0, f"an overnight task committed onto {branch} of a code repository"
    assert _git(work, "rev-parse", "HEAD").stdout == head
    said = p.stderr + p.stdout
    assert "agent_workspace.py ensure" in said and "pull request" in said, (
        f"the refusal must say how to deliver instead: {said!r}")


def test_overnight_commit_on_the_task_branch_goes_through(tmp_path):
    work = _repo(tmp_path, "datacore")
    _git(work, "switch", "-q", "-c", "agent/78210bf7")
    assert _commit(work, env=UNATTENDED).returncode == 0


def test_human_commit_on_main_is_untouched(tmp_path):
    work = _repo(tmp_path, "datacore")
    assert _commit(work).returncode == 0


def test_knowledge_space_keeps_its_direct_path(tmp_path):
    work = _repo(tmp_path, "datacore-space")                # registered knowledge (2-datacore)
    assert _commit(work, env=UNATTENDED).returncode == 0


def test_unregistered_repository_is_refused_on_its_default_branch(tmp_path):
    # Unknown is not knowledge: an unclassified repo gets the code rule.
    work = _repo(tmp_path, "some-unregistered-code")
    assert _commit(work, env=UNATTENDED).returncode != 0


def test_task_workspace_is_on_the_task_branch_and_commits(tmp_path, monkeypatch):
    import agent_workspace
    work = _repo(tmp_path, "datacore")
    ws = agent_workspace.ensure(work, "78210bf7-17dd", root=tmp_path / "worktrees")
    assert ws.branch == "agent/78210bf7-17dd"
    assert _git(ws.path, "branch", "--show-current").stdout.strip() == ws.branch
    assert _commit(ws.path, env=UNATTENDED).returncode == 0
    # Re-opening the same task's workspace (a retry, a second repo step) reuses it.
    again = agent_workspace.ensure(work, "78210bf7-17dd", root=tmp_path / "worktrees")
    assert again.path == ws.path and again.branch == ws.branch
    # The shared checkout never moved off main.
    assert _git(work, "branch", "--show-current").stdout.strip() == "main"


def test_workspaces_of_two_repositories_do_not_collide(tmp_path):
    import agent_workspace
    a = _repo(tmp_path / "a", "datacore") if (tmp_path / "a").mkdir() is None else None
    b = _repo(tmp_path / "b", "datacore-dev") if (tmp_path / "b").mkdir() is None else None
    root = tmp_path / "worktrees"
    wa = agent_workspace.ensure(a, "task-1", root=root)
    wb = agent_workspace.ensure(b, "task-1", root=root)
    assert wa.path != wb.path
