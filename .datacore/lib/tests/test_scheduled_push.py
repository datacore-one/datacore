#!/usr/bin/env python3
"""Scheduled pushes: agents publish in their host's slot, not per event.

Every ledger event used to push at once, so five hosts raced for one remote
several hundred times a day and each lost race became a merge commit
(2-datacore/1-tracks/engineering/reports/2026-10-18-scheduled-push-report.md).

What these tests hold:

  * the switch defaults to the CURRENT behaviour (push at once), so merging
    the code changes nothing until a host opts in;
  * with the scheduled mode on, an agent's append() commits but does not push;
  * the slot job publishes what was deferred;
  * the owner's sessions and an explicit `urgent=True` still push at once;
  * the slot table gives every host its own minutes, every 30 minutes, and a
    table whose windows overlap is refused.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import ledger_transport as lt

SHIPPED_SLOTS = Path(lt.__file__).resolve().parents[1] / "config" / "push_slots.yaml"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


@pytest.fixture()
def space(tmp_path: Path, monkeypatch) -> Path:
    """A knowledge clone with a real origin, plus declared, disposable principals."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    git(work, "config", "core.hooksPath", str(work / ".git" / "hooks"))
    (work / "seed.txt").write_text("seed\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "seed")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "branch", "-M", "main")
    git(work, "branch", "--set-upstream-to=origin/main", "main")

    monkeypatch.setattr(lt, "classify",
                        lambda s, root=None: lt.Result(True, "knowledge", {"entry": {"category": "knowledge"}}))
    monkeypatch.setattr(lt, "_registry", lambda root: {"work": {"category": "knowledge"}})

    import actor_identity
    principals = tmp_path / "principals.yaml"
    principals.write_text("principals:\n"
                          "  owner: {kind: human, writes_as: [laptop]}\n"
                          "  worker: {kind: agent, writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", principals)
    # Nothing from this machine's own declaration may leak into the fixture.
    monkeypatch.setenv("DATACORE_IDENTITY_FILE", str(tmp_path / "identity.env"))
    monkeypatch.delenv("DATACORE_PUSH_MODE", raising=False)
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    return work


def _published(space: Path) -> bool:
    """Is local HEAD on origin/main?"""
    git(space, "fetch", "-q", "origin")
    head = git(space, "rev-parse", "HEAD").stdout.strip()
    remote = git(space, "rev-parse", "origin/main").stdout.strip()
    return head == remote


def _event(space: Path, actor: str, n: str = "one", **kw) -> lt.Result:
    return lt.append(space, actor, "item.create", {"id": n, "title": "Synthetic"}, **kw)


# ── the switch ─────────────────────────────────────────────────────────────

def test_default_mode_is_push_at_once(space):
    """Merging this changes nothing: no switch set, an agent still pushes."""
    assert lt.push_mode() == "immediate"
    assert lt.push_policy("worker") == "now"
    res = _event(space, "worker")
    assert res.ok and res.reason == "appended and published"
    assert _published(space)


def test_mode_is_read_from_the_identity_file(space, tmp_path):
    """A cron or a unit rarely exports it, so the host's declaration counts too."""
    (tmp_path / "identity.env").write_text("DATACORE_PUSH_MODE=scheduled\n")
    assert lt.push_mode() == "scheduled"


def test_an_unknown_mode_falls_back_to_push_at_once(space, monkeypatch):
    monkeypatch.setenv("DATACORE_PUSH_MODE", "sometimes")
    assert lt.push_mode() == "immediate"


# ── scheduled mode ─────────────────────────────────────────────────────────

def test_agent_append_commits_but_does_not_push(space, monkeypatch):
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    before = git(space, "rev-parse", "HEAD").stdout.strip()
    res = _event(space, "worker")
    assert res.ok, res.reason
    assert res.context["pushed"] is False
    assert "deferred" in res.reason
    assert git(space, "rev-parse", "HEAD").stdout.strip() != before   # committed
    assert git(space, "log", "-1", "--format=%s").stdout.strip() == "ledger: item.create by worker"
    assert not _published(space)                                       # not pushed


def test_the_slot_job_publishes_what_was_deferred(space, monkeypatch, tmp_path):
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    assert _event(space, "worker").ok
    assert not _published(space)
    rc = lt.publish_slot(tmp_path, "box", now=datetime(2026, 10, 18, 9, 31))
    assert rc == 0
    assert _published(space)


def test_the_slot_job_does_nothing_outside_its_slot(space, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    assert _event(space, "worker").ok
    monkeypatch.setattr(lt, "sync_all", lambda *a, **k: pytest.fail("synced outside the slot"))
    rc = lt.publish_slot(tmp_path, "box", now=datetime(2026, 10, 18, 9, 12))
    assert rc == 0
    assert "outside" in capsys.readouterr().out
    assert not _published(space)


def test_force_publishes_outside_the_slot(space, monkeypatch, tmp_path):
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    assert _event(space, "worker").ok
    assert lt.publish_slot(tmp_path, "box", now=datetime(2026, 10, 18, 9, 12), force=True) == 0
    assert _published(space)


def test_an_unknown_host_is_refused(space, tmp_path, capsys):
    assert lt.publish_slot(tmp_path, "no-such-host", now=datetime(2026, 10, 18, 9, 0)) == 2


def test_owner_session_still_pushes_at_once(space, monkeypatch):
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    assert lt.push_policy("laptop") == "now"
    res = _event(space, "laptop")
    assert res.ok and res.reason == "appended and published"
    assert _published(space)


def test_urgent_agent_event_pushes_at_once(space, monkeypatch):
    """The briefing and anything the owner is waiting on never wait for a slot."""
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    assert lt.push_policy("worker", urgent=True) == "now"
    res = _event(space, "worker", urgent=True)
    assert res.ok and res.reason == "appended and published"
    assert _published(space)


def test_an_unregistered_writer_pushes_at_once(space, monkeypatch):
    """Deferral is opted into per agent; a writer nobody declared is not held."""
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    assert lt.push_policy("stranger") == "now"


def test_explicit_push_false_is_still_deferred(space):
    """The existing caller contract holds in either mode."""
    res = _event(space, "laptop", push=False)
    assert res.ok and res.context["pushed"] is False
    assert not _published(space)


# ── the slot table ─────────────────────────────────────────────────────────

def test_shipped_slot_table_matches_the_plan():
    slots = lt.load_slots(SHIPPED_SLOTS)
    assert slots["hosts"] == {
        "box": [0, 30],
        "nightshift": [10, 40],
        "hermes": [20, 50],
        "plur-claw": [25, 55],
        "mac": [],
    }


def test_shipped_slots_do_not_overlap_and_repeat_every_30_minutes():
    slots = lt.load_slots(SHIPPED_SLOTS)
    width = slots["window_minutes"]
    taken: dict[int, str] = {}
    for host, minutes in slots["hosts"].items():
        if not minutes:
            continue
        assert len(minutes) == 2 and (minutes[1] - minutes[0]) % 60 == 30, host
        for start in minutes:
            for m in range(start, start + width):
                assert m % 60 not in taken, f"{host} overlaps {taken.get(m % 60)} at :{m % 60:02d}"
                taken[m % 60] = host


def test_mac_has_no_slot_and_the_job_says_so(space, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(lt, "sync_all", lambda *a, **k: pytest.fail("mac has no slot"))
    assert lt.publish_slot(tmp_path, "mac", now=datetime(2026, 10, 18, 9, 0)) == 0
    assert "at once" in capsys.readouterr().out


def test_overlapping_slot_table_is_refused(tmp_path):
    bad = tmp_path / "slots.yaml"
    bad.write_text("window_minutes: 5\nhosts:\n  a: [0, 30]\n  b: [3, 33]\n")
    with pytest.raises(ValueError, match="overlap"):
        lt.load_slots(bad)


@pytest.mark.parametrize("now, host, inside", [
    ((9, 0), "box", True), ((9, 4), "box", True), ((9, 5), "box", False),
    ((9, 30), "box", True), ((9, 41), "nightshift", True), ((9, 24), "hermes", True),
    ((9, 25), "hermes", False), ((9, 25), "plur-claw", True), ((9, 59), "plur-claw", True),
])
def test_in_slot(now, host, inside):
    slots = lt.load_slots(SHIPPED_SLOTS)
    assert lt.in_slot(host, datetime(2026, 10, 18, *now), slots) is inside


def test_cli_publish_slot_outside_the_slot_is_a_no_op(tmp_path):
    """The entrypoint a timer runs; outside its minutes it says so and exits 0."""
    import os
    import sys
    p = subprocess.run([sys.executable, lt.__file__, "publish-slot", "--host", "box",
                        "--root", str(tmp_path), "--at", "09:12"],
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "DATACORE_ROOT": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    assert "outside" in p.stdout
