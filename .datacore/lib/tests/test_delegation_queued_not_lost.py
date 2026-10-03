"""A delegation made while GitHub is unreachable is queued, not lost, and the owner is told so
(fleet sim 2026-10-03, break 3).

Fault F20: GitHub down 17:00-23:00. The owner's 18:30 delegation was appended and
committed on the workstation, the push failed, and the only trace was
`mac-ledger-publish`: "held 2-datacore: 0 ledger file(s) -- fetch failed; inspect
local remote configuration" -- a local cause for a remote outage, and nothing saying a
delegated task was waiting. Now the publish run:
  * names the real cause (git's own words);
  * says, per space, which recorded tasks are queued here and not yet on the remote;
  * tells the owner ONCE ("queued, not lost"), not on every run;
  * publishes them when the remote answers, and says once that they went out.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
spec = importlib.util.spec_from_file_location("lps_queue", LIB / "ledger_publish_safe.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)
import ledger_queue  # noqa: E402
from ledger.log import EventLog  # noqa: E402

DEAD = "http://127.0.0.1:9/unreachable.git"


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "mac")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "root"; root.mkdir()
    space = root / "2-space"
    _git(tmp_path, "clone", "-q", str(origin), str(space))
    _git(space, "config", "user.email", "t@t"); _git(space, "config", "user.name", "t")
    _git(space, "config", "core.hooksPath", "/dev/null")
    log = EventLog(space, "mac", keys_dir=tmp_path / "keys", registry_path=tmp_path / "reg.yaml", sign=False)
    log.append("item.create", {"id": "old-1", "title": "already published"})
    _git(space, "add", "-A"); _git(space, "commit", "-qm", "base"); _git(space, "push", "-q", "-u", "origin", "main")
    sent = []
    monkeypatch.setattr(L, "ROOT", root)
    monkeypatch.setattr(ledger_queue, "STATE", tmp_path / "state" / "ledger-queue.json")
    monkeypatch.setattr(ledger_queue, "_send", lambda text: sent.append(text) or True)
    return space, origin, log, sent


def _delegate(space, log, title="Write the night note"):
    log.append("item.create", {"id": "night-1", "title": title, "tags": ["AI", "research"], "state": "NEXT"})
    _git(space, "add", "-A", ".datacore/events")
    _git(space, "commit", "-qm", "owner: delegate the night task", "--", ".datacore/events")


def test_unreachable_remote_queues_the_delegation_and_tells_the_owner_once(fleet, capsys):
    space, origin, log, sent = fleet
    _delegate(space, log)
    _git(space, "remote", "set-url", "origin", DEAD)
    assert L.main([]) == 1
    out = capsys.readouterr().out
    assert "inspect local remote configuration" not in out
    assert "127.0.0.1" in out or "Failed to connect" in out or "Connection refused" in out, out
    assert "QUEUED, NOT LOST" in out and "Write the night note" in out, out
    assert len(sent) == 1 and "Write the night note" in sent[0] and "queued, not lost" in sent[0].lower()
    L.main([])
    assert len(sent) == 1, f"the owner was told again: {sent}"


def test_when_the_remote_answers_it_is_published_and_said_once(fleet, capsys):
    space, origin, log, sent = fleet
    _delegate(space, log)
    _git(space, "remote", "set-url", "origin", DEAD)
    L.main([])
    _git(space, "remote", "set-url", "origin", str(origin))
    assert L.main([]) == 0
    assert "night-1" in _git(origin, "show", "main:.datacore/events/mac.jsonl").stdout
    assert len(sent) == 2 and "Write the night note" in sent[1] and "published" in sent[1].lower(), sent
    L.main([])
    assert len(sent) == 2


def test_nothing_waiting_means_nothing_said(fleet, capsys):
    space, origin, log, sent = fleet
    assert L.main([]) == 0
    assert not sent
    assert ledger_queue.unpublished_creates(space) == []


def test_only_events_missing_from_the_remote_are_queued(fleet):
    space, origin, log, sent = fleet
    _delegate(space, log)
    waiting = ledger_queue.unpublished_creates(space)
    assert [w["id"] for w in waiting] == ["night-1"]
