"""The core git hooks work in a repository that does not carry the core scripts.

WHY. On an agent's host (plur-claw for Data, hermes for Tris) the global git
config points core.hooksPath at the core runner's hooks
(~/.datacore/v2-runner/.datacore/githooks), so every repository there -- the
agent's own space repo at ~/Data, the blog site -- runs the core pre-commit and
pre-push. pre-push looked for its scripts under $HOME/Data, which on those hosts
is the agent's space repo: it has a stale copy of the ownership guard and no
ledger write gate. Every push failed with "can't open file
.../Data/.datacore/lib/hooks/ledger_write_gate.py", and Data's blog stopped
publishing on 2026-09-27. Where $HOME/Data held neither script the gate was
skipped without a word.

The hooks now resolve their scripts from their own checkout (the directory
the hook file lives in), a missing gate fails naming the file, and a ledger
space is still gated.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.events import compute_hash  # noqa: E402
from ledger.log import EventLog  # noqa: E402

GITHOOKS = LIB.parent / "githooks"


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATA_DIR", "SKIP_PRE_PUSH", "NIGHTSHIFT_RUN", "GIT_DIR", "GIT_INDEX_FILE")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", SKIP_LOCAL_CI="1", SKIP_FORMAL="1",
               # The ownership guard's identity: no registry here, this host writes miles.jsonl.
               DATACORE_ROOT=str(home), DATACORE_ACTOR="miles")
    return env


def _git(cwd: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, capture_output=True, text=True, env=env, timeout=180)


def _agent_host(tmp_path: Path, hooks: Path, stale_data: bool = True):
    """A repo with no .datacore/lib, hooks from `hooks`, and a $HOME/Data like plur-claw's."""
    home = tmp_path / "home"
    home.mkdir()
    if stale_data:
        # The agent's space repo: a stale ownership guard, no ledger write gate.
        g = home / "Data" / ".datacore" / "lib" / "hooks"
        g.mkdir(parents=True)
        (g / "log_ownership_guard.py").write_text("raise SystemExit(0)\n")
    env = _env(home)
    remote = tmp_path / "remote.git"
    _git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(remote))
    repo = tmp_path / "blog"
    _git(tmp_path, env, "init", "-q", "-b", "main", str(repo))
    _git(repo, env, "config", "core.hooksPath", str(hooks))
    _git(repo, env, "remote", "add", "origin", str(remote))
    return repo, env


def _commit(repo: Path, env: dict, name: str, text: str) -> subprocess.CompletedProcess:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    _git(repo, env, "add", name)
    return _git(repo, env, "commit", "-q", "-m", f"add {name}")


@pytest.mark.parametrize("stale_data", [True, False], ids=["stale-home-Data", "no-home-Data"])
def test_commit_and_push_succeed_in_a_repo_without_core_scripts(tmp_path, stale_data):
    repo, env = _agent_host(tmp_path, GITHOOKS, stale_data)
    c = _commit(repo, env, "content/posts/one.md", "# One\n")
    assert c.returncode == 0, c.stderr
    p = _git(repo, env, "push", "-q", "origin", "main")
    assert p.returncode == 0, p.stderr
    assert "can't open file" not in p.stderr, p.stderr


def _ledger_space(tmp_path: Path, stale_data: bool):
    repo, env = _agent_host(tmp_path, GITHOOKS, stale_data)
    EventLog(repo, "miles").append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    _git(repo, env, "add", ".datacore/events/miles.jsonl")
    assert _git(repo, env, "commit", "-q", "-m", "base").returncode == 0
    assert _git(repo, env, "push", "-q", "origin", "main").returncode == 0
    return repo, env


def _forge(repo: Path) -> None:
    """A hand-written event line: not the canonical bytes EventLog writes."""
    path = repo / ".datacore" / "events" / "miles.jsonl"
    last = json.loads(path.read_text().splitlines()[-1])
    body = {"seq": last["seq"] + 1, "hlc": last["hlc"], "actor": "miles", "type": "item.create",
            "payload": {"id": "t2"}, "prev": last["hash"]}
    h = compute_hash(body)
    with path.open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": h}) + "\n")


@pytest.mark.parametrize("stale_data", [True, False], ids=["stale-home-Data", "no-home-Data"])
def test_a_ledger_space_is_still_gated_at_commit(tmp_path, stale_data):
    repo, env = _ledger_space(tmp_path, stale_data)
    _forge(repo)
    _git(repo, env, "add", ".datacore/events/miles.jsonl")
    c = _git(repo, env, "commit", "-q", "-m", "forged")
    assert c.returncode != 0, "a hand-written ledger line was committed"
    assert "ledger-write-gate: REFUSED" in c.stderr, c.stderr


@pytest.mark.parametrize("stale_data", [True, False], ids=["stale-home-Data", "no-home-Data"])
def test_a_ledger_space_is_still_gated_at_push(tmp_path, stale_data):
    repo, env = _ledger_space(tmp_path, stale_data)
    _forge(repo)
    _git(repo, env, "add", ".datacore/events/miles.jsonl")
    # Committed with hooks off, as a hand edit would be; the push must still refuse it.
    assert _git(repo, env, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "forged").returncode == 0
    p = _git(repo, env, "push", "-q", "origin", "main")
    assert p.returncode != 0, "a hand-written ledger line was pushed"
    assert "ledger-write-gate: REFUSED" in p.stderr, p.stderr


def test_a_missing_gate_fails_naming_the_file(tmp_path):
    """Hooks copied away from any checkout, no $HOME/Data: refuse, and say what is missing."""
    hooks = tmp_path / "loose" / ".datacore" / "githooks"
    shutil.copytree(GITHOOKS, hooks)
    repo, env = _agent_host(tmp_path, hooks, stale_data=False)
    c = _commit(repo, env, "a.md", "a\n")
    assert c.returncode != 0
    assert "ledger_write_gate.py" in c.stderr and "not found" in c.stderr, c.stderr
    assert _git(repo, env, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "a").returncode == 0
    p = _git(repo, env, "push", "-q", "origin", "main")
    assert p.returncode != 0
    assert "ledger_write_gate.py" in p.stderr and "not found" in p.stderr, p.stderr
