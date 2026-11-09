"""T3 (ledger-upgrade Phase 1, audit D7): an event whose HLC is further in the
future than the tolerance is refused at write (the real pre-commit hook),
flagged by verify, and never followed: the other writers in the space keep
appending at real time.

The event under test is otherwise perfect -- canonical bytes, the right actor,
a correct hash and chain -- so only its clock can be what is refused. That is
the D7 case: no malice, a host with a wrong clock or a script that writes
microseconds where milliseconds belong.

Promise LED-7 (test_promise_LED7_one_bad_record.py) pins the floor and the
verify flag; LED-3 pins the gate function. This eval puts the three halves of
the plan's claim in one place and drives the write half through git.

Seeded failures (any one turns this red):
  * the HLC check removed from ledger_write_gate.check_change;
  * the future-HLC flag removed from ledger.verify;
  * EventLog.append taking a sibling's far-future HLC as its causal floor
    again (the 2026-09 reproduction: year-2100 floor, counter overflow on the
    10th append).

Runs in disposable directories under tmp; never touches a live space.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import warnings
from pathlib import Path

from ledger.events import Event, body_dict, compute_hash, to_line
from ledger.hlc import parse
from ledger.log import EventLog
from ledger.verify import verify_chain

LIB = Path(__file__).resolve().parents[2]
HOOKS = LIB.parent / "githooks"
DAY_MS = 24 * 3600 * 1000
TOLERANCE_MS = 10 * 60 * 1000


def _future_line(space: Path, actor: str, ahead_ms: int) -> Path:
    """Append one canonical, correctly hashed event dated `ahead_ms` ahead."""
    path = space / ".datacore" / "events" / f"{actor}.jsonl"
    last = json.loads(path.read_text().splitlines()[-1])
    hlc = f"{int(time.time() * 1000) + ahead_ms}.0000.{actor}"
    body = body_dict(last["seq"] + 1, hlc, actor, "item.create",
                     {"id": "future", "title": "from a wrong clock", "state": "NEXT"}, last["hash"])
    with path.open("a") as f:
        f.write(to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n")
    return path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={HOOKS}", *args],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=300)


def test_a_future_hlc_is_refused_at_commit_and_a_present_one_is_not(tmp_path):
    repo = tmp_path / "9-fixture"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    log = EventLog(repo, "alice")
    log.append("item.create", {"id": "a1", "title": "a1", "state": "NEXT"})
    _git(repo, "add", ".datacore/events/alice.jsonl")
    assert _git(repo, "commit", "-q", "-m", "base").returncode == 0

    _future_line(repo, "alice", DAY_MS)
    _git(repo, "add", ".datacore/events/alice.jsonl")
    c = _git(repo, "commit", "-q", "-m", "a day ahead")
    out = c.stdout + c.stderr
    assert c.returncode != 0 and "ledger-write-gate: REFUSED" in out and "in the future" in out, (
        "expected: the real pre-commit hook refuses an event dated a day ahead, naming the future HLC."
        f"\nseen: exit {c.returncode}\n{out}")

    # Control: the same kind of line within tolerance commits (the refusal is
    # about the clock, not the shape of the line).
    _git(repo, "reset", "-q", "--hard", "HEAD")
    _future_line(repo, "alice", 60 * 1000)
    _git(repo, "add", ".datacore/events/alice.jsonl")
    ok = _git(repo, "commit", "-q", "-m", "a minute ahead")
    assert ok.returncode == 0, f"a line one minute ahead (within tolerance) must commit:\n{ok.stderr}"


def test_verify_flags_a_future_hlc(tmp_path):
    space = tmp_path / "9-fixture"
    EventLog(space, "alice").append("item.create", {"id": "a1", "title": "a1", "state": "NEXT"})
    path = _future_line(space, "alice", DAY_MS)
    errors = verify_chain(path)
    assert any("future" in e.lower() for e in errors), (
        f"expected: verify flags an event dated a day ahead.\nseen: {errors or 'no problems reported'}")


def test_the_other_writers_keep_appending_at_real_time(tmp_path):
    space = tmp_path / "9-fixture"
    EventLog(space, "alice").append("item.create", {"id": "a1", "title": "a1", "state": "NEXT"})
    bob = EventLog(space, "bob")
    bob.append("item.create", {"id": "b1", "title": "b1", "state": "NEXT"})
    # Year 2100, the audit's reproduction.
    path = space / ".datacore" / "events" / "alice.jsonl"
    last = json.loads(path.read_text().splitlines()[-1])
    body = body_dict(last["seq"] + 1, "4102444800000.9990.alice", "alice", "item.create",
                     {"id": "y2100", "title": "y2100", "state": "NEXT"}, last["hash"])
    with path.open("a") as f:
        f.write(to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stamps = [bob.append("item.create", {"id": f"x{i}", "title": "x", "state": "NEXT"}).hlc
                  for i in range(15)]
    now = int(time.time() * 1000)
    worst = max(parse(h)[0] for h in stamps)
    assert worst < now + TOLERANCE_MS, (
        "expected: another writer ignores a far-future floor and keeps stamping near real time.\n"
        f"seen: bob's latest HLC is {(worst - now) // DAY_MS} days ahead ({stamps[-1]})")
