"""LED-9: If records go missing on a machine, Datacore finds them wherever they
still exist and restores them without overwriting anything.

Kind: deterministic (tmp git repos, a local bare origin). Real tool:
`ledger_restore_prefix.py` (`--find`, `--from <rev> [--apply]`).

The 2026-09-16 shape: a writer's last events sit somewhere other than the
checked-out log (a park branch), the sequence witness is ahead of the log, and
every append is refused. Promise, as evals:
  * found wherever they still exist:
      - on a local side branch (park)                       [LS-12, holds]
      - on a branch that exists only on origin (another machine parked it)
      - in a commit no branch points at any more (reset away; reflog only)
  * restored without overwriting: `--apply` writes the longer log only when
    the current one is a byte prefix of it, keeps the old bytes beside it, and
    refuses a forked copy leaving the log untouched.

Seeded failure: `--find` searched only working copies and origin/main (the
first 2026-09-16 search that wrongly called the events UNRECOVERABLE); the
origin-only and reflog-only cases are the places it still does not look.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ledger_restore_prefix as lrp
from ledger.log import EventLog
from ledger.verify import verify_chain

REL = ".datacore/events/miles.jsonl"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _config(repo: Path, hooks: Path) -> None:
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks)),
                 ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """origin with main (3 events); returns (origin, hooks)."""
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    seed = tmp_path / "seed" / "9-fixture"
    seed.parent.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True, timeout=60)
    _config(seed, hooks)
    _git(seed, "remote", "add", "origin", str(origin))
    log = EventLog(seed, "miles", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"t{i}", "title": f"task {i}", "state": "NEXT"})
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "three events")
    assert _git(seed, "push", "-q", "origin", "main").returncode == 0
    return origin, hooks, seed


def _clone(origin: Path, dest: Path, hooks: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, timeout=60)
    _config(dest, hooks)
    return dest


def _extend_and_commit(space: Path, n: int, msg: str) -> str:
    log = EventLog(space, "miles", sign=False)
    for i in range(n):
        log.append("item.create", {"id": f"late{i}", "title": "late", "state": "NEXT"})
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", msg)
    return _git(space, "rev-parse", "HEAD").stdout.strip()


def _found(space: Path) -> list[int]:
    return [row[1] for row in lrp.find(space, "miles", "2000-01-01")]


def test_found_on_a_local_park_branch_and_restored(world, tmp_path, capsys):
    origin, hooks, _ = world
    space = _clone(origin, tmp_path / "mac" / "9-fixture", hooks)
    _git(space, "checkout", "-qb", "park/2026-09-08")
    park = _extend_and_commit(space, 2, "parked")
    _git(space, "checkout", "-q", "main")
    before = (space / REL).read_bytes()

    assert _found(space) == [4], "the parked events were not found"
    assert lrp.main(["--space", str(space), "--actor", "miles", "--from", park, "--apply"]) == 0
    after = (space / REL).read_bytes()
    assert after.startswith(before) and after.count(b"\n") == 5
    assert (space / (REL + ".pre-restore")).read_bytes() == before, "old bytes not kept"
    assert verify_chain(space / REL) == []


def test_found_when_they_exist_only_on_origin(world, tmp_path):
    """Another machine parked the events on origin; this one never fetched that branch."""
    origin, hooks, _ = world
    other = _clone(origin, tmp_path / "box" / "9-fixture", hooks)
    _git(other, "checkout", "-qb", "park/box")
    _extend_and_commit(other, 2, "parked on the box")
    assert _git(other, "push", "-q", "origin", "park/box").returncode == 0
    here = _clone(origin, tmp_path / "mac" / "9-fixture", hooks)
    _git(here, "update-ref", "-d", "refs/remotes/origin/park/box")   # as if cloned before the push

    assert _found(here) == [4], "events that exist only on origin were not found"


def test_found_in_a_commit_no_branch_points_at(world, tmp_path):
    origin, hooks, _ = world
    space = _clone(origin, tmp_path / "mac" / "9-fixture", hooks)
    _extend_and_commit(space, 2, "later reset away")
    _git(space, "reset", "-q", "--hard", "HEAD~1")
    assert (space / REL).read_text().count("\n") == 3

    assert _found(space) == [4], "events in a reset-away commit (reflog only) were not found"


def test_a_forked_copy_is_refused_and_nothing_is_overwritten(world, tmp_path):
    origin, hooks, _ = world
    space = _clone(origin, tmp_path / "mac" / "9-fixture", hooks)
    _git(space, "checkout", "-qb", "park/x")
    park = _extend_and_commit(space, 2, "parked")
    _git(space, "checkout", "-q", "main")
    # This machine appended a different event 3 meanwhile: the park copy is a fork.
    EventLog(space, "miles", sign=False).append("item.create", {"id": "local", "title": "l", "state": "NEXT"})
    before = (space / REL).read_bytes()

    assert lrp.main(["--space", str(space), "--actor", "miles", "--from", park, "--apply"]) != 0
    assert (space / REL).read_bytes() == before, "a refused restore changed the log"
