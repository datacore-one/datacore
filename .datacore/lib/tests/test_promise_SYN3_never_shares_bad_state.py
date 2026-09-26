"""SYN-3: Sync never shares a half-finished merge, conflict markers, or a file
too large for the shared copy.

Kind: deterministic. Every sync entry point that publishes a space, run
against a tmp clone with a local bare origin:
  * converge   -- `ledger_transport.converge` (the hourly phase-1 cycle, `sync`)
  * fleet      -- `git_fleet_sync.sync_repo(execute=True, pull=True)` (agent hosts)
  * cos_sync   -- the box's `chief-of-staff/server/lib/cos_sync.sh` (every 15 min
                  on winston; it autosaves with its own `git add -A` first)

Scenarios, each checked on ORIGIN after the sync:
  * dirty tree: a new note with leftover conflict markers, and a 60 MB file
    (the fleet's own limit is 50 MB; GitHub refuses 100 MB) -- neither may
    reach origin;
  * half-finished merge: a merge stopped on a conflict (MERGE_HEAD, unmerged
    file) -- no conflict markers may reach origin.

Seeded failure: the pre-datacore#28 autosave -- `git add -A && git commit`
with no in-progress, marker or size check (exactly what cos_sync.sh still does
before calling converge).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
COS_SYNC = LIB.parent / "modules" / "chief-of-staff" / "server" / "lib" / "cos_sync.sh"
BIG = 60 * 1024 * 1024
MARKED = "intro\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> origin/main\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    root = tmp_path / "Data"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "repositories.yaml").write_text(
        "repositories:\n  9-fixture:\n    category: knowledge\n")
    space = root / "9-fixture"
    subprocess.run(["git", "init", "-q", "-b", "main", str(space)], check=True, timeout=60)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
        _git(space, "config", k, v)
    _git(space, "remote", "add", "origin", str(origin))
    (space / "note.md").write_text("one\ntwo\nthree\n")
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", "seed")
    assert _git(space, "push", "-q", "-u", "origin", "main").returncode == 0
    _git(space, "remote", "set-head", "origin", "main")
    return root, space, origin, hooks


def _dirty(space: Path) -> None:
    (space / "conflicted.md").write_text(MARKED)
    with open(space / "big.bin", "wb") as f:
        f.truncate(BIG)


def _half_merged(space: Path, origin: Path, hooks: Path, tmp: Path) -> None:
    other = tmp / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, timeout=60)
    for k, v in (("user.email", "o@t"), ("user.name", "o"), ("core.hooksPath", str(hooks))):
        _git(other, "config", k, v)
    (other / "note.md").write_text("one\nTWO from origin\nthree\n")
    _git(other, "commit", "-qam", "origin edit")
    assert _git(other, "push", "-q", "origin", "main").returncode == 0
    (space / "note.md").write_text("one\nTWO from here\nthree\n")
    _git(space, "commit", "-qam", "local edit")
    _git(space, "fetch", "-q", "origin")
    assert _git(space, "merge", "origin/main").returncode != 0
    assert (space / ".git" / "MERGE_HEAD").exists()


def _run(entry: str, root: Path, space: Path, tmp: Path, monkeypatch) -> None:
    if entry == "converge":
        import ledger_transport as lt
        lt.converge(space, root=root)
    elif entry == "fleet":
        import git_fleet_sync
        git_fleet_sync.sync_repo(space, execute=True, pull=True)
    else:
        lib = root / ".datacore" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        os.symlink(LIB / "ledger_transport.py", lib / "ledger_transport.py")
        (lib / "cos_alert.sh").write_text("#!/bin/sh\necho \"$*\" >> \"$HOME/alerts\"\n")
        (lib / "cos_alert.sh").chmod(0o755)
        home = tmp / "home"
        (home / ".datacore" / "cos").mkdir(parents=True)
        env = {**os.environ, "HOME": str(home), "DATACORE_HOME": str(root), "DATACORE_ROOT": str(root),
               "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
               "GIT_CONFIG_GLOBAL": "/dev/null"}
        subprocess.run(["bash", str(COS_SYNC)], env=env, capture_output=True, text=True, timeout=60)


def _origin_files(origin: Path) -> dict[str, str]:
    ls = subprocess.run(["git", "-C", str(origin), "ls-tree", "-r", "-l", "main"],
                        capture_output=True, text=True, timeout=60).stdout
    out = {}
    for line in ls.splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[3]
    return out


def _markers_on_origin(origin: Path) -> list[str]:
    bad = []
    for path in _origin_files(origin):
        text = subprocess.run(["git", "-C", str(origin), "show", f"main:{path}"],
                              capture_output=True, text=True, timeout=60, errors="replace").stdout
        if "<<<<<<<" in text or ">>>>>>>" in text:
            bad.append(path)
    return bad


ENTRIES = ["converge", "fleet", "cos_sync"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_markers_and_oversized_files_never_reach_origin(entry, world, tmp_path, monkeypatch):
    root, space, origin, _ = world
    _dirty(space)
    _run(entry, root, space, tmp_path, monkeypatch)
    files = _origin_files(origin)
    big = {p: s for p, s in files.items() if s.isdigit() and int(s) >= 50 * 1024 * 1024}
    marked = _markers_on_origin(origin)
    assert not big and not marked, (f"{entry} shared: too-large files {big or 'none'}; "
                                    f"conflict markers in {marked or 'none'}")


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_half_finished_merge_never_reaches_origin(entry, world, tmp_path, monkeypatch):
    root, space, origin, hooks = world
    _half_merged(space, origin, hooks, tmp_path)
    _run(entry, root, space, tmp_path, monkeypatch)
    bad = _markers_on_origin(origin)
    assert not bad, f"{entry} shared a half-finished merge (markers in {bad})"
