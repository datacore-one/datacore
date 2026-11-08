"""A log that never reached origin may be removed; a published one never.

WHY. On 2026-11-07 a promise-eval fixture event leaked into a host's real
2-datacore as `.datacore/events/promise-eval.jsonl`, committed locally only.
The relay then refused every push from that host ("chain fails verification"),
and the commit gate refused removing the file ("log deleted (the ledger is
append-only)"). The two guards deadlocked: 115 commits were held back with no
sanctioned way out. A log no remote has ever held is not shared history, so
removing it rewrites nothing anyone else has seen.

The boundary: a log any remote-tracking ref has ever held stays undeletable,
and with no remote-tracking refs at all nothing counts as unpublished.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[1]
GATE = LIB / "hooks" / "ledger_write_gate.py"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]
LOG = ".datacore/events/promise-eval.jsonl"


def _git(repo, *args):
    return subprocess.run([*GIT, *args], cwd=repo, capture_output=True, text=True)


def _gate(repo, *args):
    return subprocess.run([sys.executable, str(GATE), "--repo", str(repo), *args],
                          capture_output=True, text=True)


def _setup(tmp_path, publish_log: bool, with_remote: bool = True):
    remote = tmp_path / "origin.git"
    repo = tmp_path / "2-space"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    own = EventLog(repo, "miles", keys_dir=tmp_path / "keys", registry_path=tmp_path / "reg.yaml")
    own.append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    _git(repo, "add", ".datacore/events/miles.jsonl")
    assert _git(repo, "commit", "-qm", "base").returncode == 0
    if with_remote and not publish_log:
        _git(tmp_path, "init", "-q", "--bare", str(remote))
        _git(repo, "remote", "add", "origin", str(remote))
        assert _git(repo, "push", "-q", "origin", "main").returncode == 0
    stray = EventLog(repo, "promise-eval", keys_dir=tmp_path / "keys", registry_path=tmp_path / "reg.yaml")
    stray.append("item.create", {"id": "x1", "title": "fixture", "state": "NEXT"})
    _git(repo, "add", LOG)
    assert _git(repo, "commit", "-qm", "leak").returncode == 0
    if with_remote and publish_log:
        _git(tmp_path, "init", "-q", "--bare", str(remote))
        _git(repo, "remote", "add", "origin", str(remote))
        assert _git(repo, "push", "-q", "origin", "main").returncode == 0
    assert _git(repo, "rm", "-q", LOG).returncode == 0
    return repo


def test_removing_a_log_no_remote_ever_held_is_accepted(tmp_path):
    repo = _setup(tmp_path, publish_log=False)
    r = _gate(repo, "--staged")
    assert r.returncode == 0, r.stderr


def test_removing_a_published_log_is_still_refused(tmp_path):
    repo = _setup(tmp_path, publish_log=True)
    r = _gate(repo, "--staged")
    assert r.returncode == 1 and "log deleted" in r.stderr


def test_a_log_published_once_and_later_removed_on_origin_is_still_refused(tmp_path):
    repo = _setup(tmp_path, publish_log=True)
    # origin's tip no longer has it, but its history did: still shared history.
    clone = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(clone))
    _git(clone, "rm", "-q", LOG)
    _git(clone, "commit", "-qm", "drop")
    _git(clone, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    r = _gate(repo, "--staged")
    assert r.returncode == 1 and "log deleted" in r.stderr


def test_with_no_remote_at_all_removal_is_refused(tmp_path):
    repo = _setup(tmp_path, publish_log=False, with_remote=False)
    r = _gate(repo, "--staged")
    assert r.returncode == 1 and "log deleted" in r.stderr


def test_editing_an_unpublished_log_is_still_refused(tmp_path):
    repo = _setup(tmp_path, publish_log=False)
    _git(repo, "reset", "-q", "HEAD", "--", LOG)  # test fixture only: undo the staged rm
    _git(repo, "checkout", "-q", "--", LOG)
    p = repo / LOG
    p.write_text(p.read_text() + '{"hand": "written"}\n')
    _git(repo, "add", LOG)
    r = _gate(repo, "--staged")
    assert r.returncode == 1
