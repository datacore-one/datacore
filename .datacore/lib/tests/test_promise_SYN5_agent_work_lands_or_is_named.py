"""SYN-5: Work an agent did on any machine reaches the shared copy within a day,
or I am told which machine and branch it is stuck on.

Kind: deterministic. The agent-host lander `git_fleet_sync.py --execute --pull`
(datacore-fleet-sync.timer on nightshift, hermes, plur-claw, winston) run by
`main()` against a tmp data root holding one space clone and a local bare
origin. Its exit status is what fires `fleet_sync_alert.sh` (OnFailure).

Promise, as evals:
  * uncommitted agent work on the default branch reaches origin in one run;
  * work committed on a side branch that has sat unpushed for two days is
    either on origin after the run, or the run fails (so the alert fires) and
    its output names the repo, the branch and this machine;
  * the same for a local-only branch (no upstream) while the checkout itself
    is on main -- the case no test covered (SYN-5 evidence).

Seeded failure: `held` side-branch repos print under "Held back" and the run
exits 0, so the unit is green and no alert is sent -- the stuck work is named
only in a journal nobody reads.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import git_fleet_sync

TWO_DAYS_AGO = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 2 * 86400))


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60,
                          env=env)


@pytest.fixture
def host(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    root = tmp_path / "Data"
    space = root / "9-fixture"
    space.parent.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(space)], check=True, timeout=60)
    for k, v in (("user.email", "agent@t"), ("user.name", "agent"), ("core.hooksPath", str(hooks))):
        _git(space, "config", k, v)
    _git(space, "remote", "add", "origin", str(origin))
    (space / "note.md").write_text("base\n")
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", "seed")
    assert _git(space, "push", "-q", "-u", "origin", "main").returncode == 0
    _git(space, "remote", "set-head", "origin", "main")
    return root, space, origin


def _old_commit(space: Path, path: str, text: str) -> None:
    (space / path).write_text(text)
    _git(space, "add", path)
    env = {**os.environ, "GIT_AUTHOR_DATE": TWO_DAYS_AGO, "GIT_COMMITTER_DATE": TWO_DAYS_AGO}
    assert _git(space, "commit", "-qm", f"agent work: {path}", env=env).returncode == 0


def _run(root: Path, monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(root), "--execute", "--pull"])
    code = git_fleet_sync.main()
    return code, capsys.readouterr().out


def _on_origin(origin: Path, text: str) -> bool:
    refs = subprocess.run(["git", "-C", str(origin), "for-each-ref", "--format=%(refname)"],
                          capture_output=True, text=True, timeout=30).stdout.split()
    return any(text in subprocess.run(["git", "-C", str(origin), "log", "-p", ref],
                                      capture_output=True, text=True, timeout=30).stdout for ref in refs)


def test_uncommitted_work_on_main_reaches_origin(host, monkeypatch, capsys):
    root, space, origin = host
    (space / "research.md").write_text("findings from the agent\n")
    code, out = _run(root, monkeypatch, capsys)
    assert code == 0, out
    assert _on_origin(origin, "findings from the agent"), out


def _told(code: int, out: str, branch: str) -> list[str]:
    missing = []
    if code == 0:
        missing.append("the run exited 0, so no alert fires")
    for what in ("9-fixture", branch, socket.gethostname().split(".")[0]):
        if what not in out:
            missing.append(f"output does not name {what!r}")
    return missing


def test_work_stuck_on_a_side_branch_is_landed_or_named(host, monkeypatch, capsys):
    root, space, origin = host
    _git(space, "checkout", "-qb", "agent/geo-research")
    _old_commit(space, "geo.md", "stuck research\n")
    code, out = _run(root, monkeypatch, capsys)
    if _on_origin(origin, "stuck research"):
        return
    missing = _told(code, out, "agent/geo-research")
    assert not missing, "two-day-old work on a side branch is neither shared nor reported: " + "; ".join(missing)


def test_work_on_a_local_only_branch_is_landed_or_named(host, monkeypatch, capsys):
    root, space, origin = host
    _git(space, "checkout", "-qb", "agent/draft")
    _old_commit(space, "draft.md", "unpushed draft\n")
    _git(space, "checkout", "-q", "main")
    code, out = _run(root, monkeypatch, capsys)
    if _on_origin(origin, "unpushed draft"):
        return
    missing = _told(code, out, "agent/draft")
    assert not missing, "two-day-old work on a local-only branch is neither shared nor reported: " + "; ".join(missing)
