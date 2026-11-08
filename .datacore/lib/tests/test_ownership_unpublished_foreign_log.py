"""A foreign log added and removed again before any push publishes nothing foreign.

WHY. On 2026-11-07 a promise-eval fixture log leaked into Miles's real
2-datacore in a local commit. Removing it (allowed by the commit gate since
#306) still left both commits, the add and the remove, in the unpublished
range, and the ownership guard judges commits one by one, so it refused every
push: "'miles' modified another actor's event log: promise-eval.jsonl". The
tip being pushed does not contain the log and no remote ever held it, so the
push carries nothing written into another actor's log.

The boundary: a foreign log present at the pushed tip is refused, and a
foreign log any remote has ever held is refused even if the tip drops it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
GUARD = LIB / "hooks" / "log_ownership_guard.py"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]
STRAY = ".datacore/events/promise-eval.jsonl"
RUN_AS = """
import socket, sys, runpy
socket.gethostname = lambda: {host!r}
sys.argv = ["log_ownership_guard.py", *{ranges!r}]
runpy.run_path({guard!r}, run_name="__main__")
"""


def _git(repo, *a):
    return subprocess.run([*GIT, *a], cwd=repo, capture_output=True, text=True, timeout=60)


@pytest.fixture
def space(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    sp = tmp_path / "2-datacore"
    subprocess.run(["git", "clone", "-q", str(remote), str(sp)], check=True, capture_output=True)
    _git(sp, "checkout", "-q", "-b", "main")
    EventLog(sp, "miles").append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    _git(sp, "add", "-A"); _git(sp, "commit", "-q", "-m", "base"); _git(sp, "push", "-q", "origin", "main")
    return sp


def _leak(sp):
    EventLog(sp, "promise-eval").append("item.create", {"id": "x1", "title": "fixture", "state": "NEXT"})
    _git(sp, "add", "-A"); _git(sp, "commit", "-q", "-m", "leak")


def _own_work(sp):
    EventLog(sp, "miles").append("item.create", {"id": "t2", "title": "two", "state": "NEXT"})
    _git(sp, "add", "-A"); _git(sp, "commit", "-q", "-m", "own work")


def _drop(sp):
    _git(sp, "rm", "-q", STRAY); _git(sp, "commit", "-q", "-m", "drop leak")


def _check(sp):
    base = _git(sp, "rev-parse", "origin/main").stdout.strip()
    tip = _git(sp, "rev-parse", "HEAD").stdout.strip()
    code = RUN_AS.format(host="miles-test-host", ranges=[f"{base}..{tip}"], guard=str(GUARD))
    return subprocess.run([sys.executable, "-c", code], cwd=sp, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "DATACORE_ROOT": str(ROOT), "DATACORE_ACTOR": "miles"})


def test_a_foreign_log_added_then_removed_before_any_push_passes(space):
    _leak(space); _own_work(space); _drop(space)
    r = _check(space)
    assert r.returncode == 0, r.stderr


def test_a_foreign_log_still_present_at_the_tip_is_refused(space):
    _leak(space); _own_work(space)
    r = _check(space)
    assert r.returncode == 1 and "promise-eval" in r.stderr


def test_a_foreign_log_a_remote_once_held_is_refused_even_when_dropped(space):
    _leak(space)
    _git(space, "push", "-q", "origin", "main")          # published once
    EventLog(space, "promise-eval").append("item.create", {"id": "x2", "title": "more", "state": "NEXT"})
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "write foreign log")
    _drop(space)
    r = _check(space)
    assert r.returncode == 1 and "promise-eval" in r.stderr
