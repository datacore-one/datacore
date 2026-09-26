"""AGT-10: Only an agent's own log can hold its records. No agent, script or
hand edit can add a record in another agent's name.

Kind: deterministic. Every write path, against tmp git repos:
  * the commit/push content gate (hooks/ledger_write_gate.py) -- the actor field;
  * the pre-push ownership guard (hooks/log_ownership_guard.py) run as the
    hermes host (tris) pushing a genuine EventLog line in miles.jsonl;
  * the library itself (ledger.log.EventLog) called by a script on a host whose
    declared actor is tris, appending as miles;
  * an agent's tool call (tool_policy.decide, the policy every executor's hook
    applies) writing into another agent's log.
Reworded 2026-09-26: no signing yet; the write gate (actor = log file) is the
guarantee.

Seeded failure: 2026-09-25 forged cadence records (a9aa456d) reused a real run
id in another writer's log. Verified by disabling the gate's actor check and
the guard's foreign-log check.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.events import Event, body_dict, compute_hash, to_line
from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
sys.path.insert(0, str(LIB / "hooks"))
import ledger_write_gate as G  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]


def _git(repo, *a):
    return subprocess.run([*GIT, *a], cwd=repo, capture_output=True, text=True, timeout=60)


@pytest.fixture
def space(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    sp = tmp_path / "5-plur"
    subprocess.run(["git", "clone", "-q", str(remote), str(sp)], check=True, capture_output=True)
    _git(sp, "checkout", "-q", "-b", "main")
    EventLog(sp, "miles").append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    EventLog(sp, "tris").append("item.create", {"id": "t2", "title": "two", "state": "NEXT"})
    _git(sp, "add", "-A"); _git(sp, "commit", "-q", "-m", "base"); _git(sp, "push", "-q", "origin", "main")
    return sp


def _miles_line_by(sp, actor):
    p = sp / ".datacore" / "events" / "miles.jsonl"
    last = json.loads(p.read_text().splitlines()[-1])
    body = body_dict(last["seq"] + 1, f"{int(last['hlc'].split('.')[0]) + 1}.0000.{actor}", actor,
                     "metric.attest", {"metric": "cadence.run", "phase": "end", "result": "ok",
                                       "run_id": "a9aa456d"}, last["hash"])
    return p, (to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n").encode()


def test_the_content_gate_refuses_a_record_whose_actor_is_not_the_log(space):
    p, line = _miles_line_by(space, "tris")
    old = p.read_bytes()
    errs = G.check_change(".datacore/events/miles.jsonl", old, old + line)
    assert any("not this log's writer" in e for e in errs), errs
    p2, own = _miles_line_by(space, "miles")
    assert G.check_change(".datacore/events/miles.jsonl", old, old + own) == []


GUARD_AS = """
import socket, sys, runpy
socket.gethostname = lambda: {host!r}
sys.argv = ["log_ownership_guard.py", *{ranges!r}]
runpy.run_path({guard!r}, run_name="__main__")
"""


def _push_check_as(sp, host):
    base = _git(sp, "rev-parse", "origin/main").stdout.strip()
    tip = _git(sp, "rev-parse", "HEAD").stdout.strip()
    code = GUARD_AS.format(host=host, ranges=[f"{base}..{tip}"], guard=str(LIB / "hooks" / "log_ownership_guard.py"))
    return subprocess.run([sys.executable, "-c", code], cwd=sp, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "DATACORE_ROOT": str(ROOT)})


def test_the_push_guard_refuses_a_host_writing_another_agents_log(space):
    """hermes (tris's host) appends a perfectly canonical miles record and tries to push it."""
    EventLog(space, "miles").append("metric.attest", {"metric": "cadence.run", "phase": "end", "result": "ok"})
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "forged as miles")
    r = _push_check_as(space, "transporter")
    assert r.returncode == 1 and "miles.jsonl" in r.stderr, r.stdout + r.stderr


def test_the_push_guard_lets_an_agent_publish_its_own_log(space):
    EventLog(space, "tris").append("metric.attest", {"metric": "cadence.run", "phase": "end", "result": "ok"})
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "own")
    r = _push_check_as(space, "transporter")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_script_cannot_append_in_another_agents_name(space, monkeypatch):
    """On tris's host a script opens EventLog(space, 'miles'): the library must refuse, not write."""
    monkeypatch.setenv("DATACORE_ACTOR", "tris")
    import actor_identity
    monkeypatch.setattr(actor_identity, "this_actor", lambda strict=False: "tris")
    p = space / ".datacore" / "events" / "miles.jsonl"
    before = p.read_bytes()
    try:
        EventLog(space, "miles").append("metric.attest", {"metric": "cadence.run", "phase": "end", "result": "ok"})
    except Exception:  # noqa: BLE001 -- any refusal is the promise holding
        pass
    assert p.read_bytes() == before, "a process running as tris wrote a record into miles.jsonl"


@pytest.mark.parametrize("principal, victim", [("tris", "miles"), ("data", "winston"), ("miles", "tris"), ("winston", "data")])
@pytest.mark.parametrize("tool", ["Edit", "Write", "Bash"])
def test_an_agent_tool_call_into_another_agents_log_is_refused(principal, victim, tool):
    import tool_policy
    path = f"/srv/datacore/6-meridian/.datacore/events/{victim}.jsonl"
    inp = {"Edit": {"file_path": path, "old_string": "", "new_string": "{}"},
           "Write": {"file_path": path, "content": "{}\n"},
           "Bash": {"command": f"printf '%s\\n' '{{\"actor\":\"{victim}\"}}' >> {path}"}}[tool]
    d = tool_policy.decide(principal, tool, inp)
    assert not d.allow, f"{principal} may {tool} into {victim}.jsonl: {d.reason}"
