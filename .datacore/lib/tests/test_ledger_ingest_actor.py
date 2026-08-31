"""The hourly sweep must admit tasks under THIS HOST's actor.

DIP-0046 §151: "One writer per file. Enforced by filename (<actor>.jsonl)."
That is what makes `(actor, seq)` identify exactly one event forever.

`genesis` was the correct actor for a one-shot migration, run once on one
machine. This sweep runs hourly on every host, so leaving the default made
two machines increment independent counters into the same genesis.jsonl and
emit different events under the same seq whenever both saw a new task before
syncing — resolved by hand three times in one session (5-plur seq 561,
0-personal seq 918-927, 5-plur seq 568).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import ledger_ingest_org as ingest  # noqa: E402

ORG = """\
#+TITLE: Next Actions

* NEXT A brand new task
:PROPERTIES:
:ID: task-fresh
:END:
"""


@pytest.fixture
def space(tmp_path):
    space = tmp_path / "0-testspace"
    (space / "org").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(ORG, encoding="utf-8")
    return space


def test_new_tasks_are_admitted_under_the_host_actor(space, monkeypatch):
    monkeypatch.setattr(ingest, "_this_actor", lambda: "testhost")
    from ledger.genesis import import_space, scan

    assert len(scan(space).importable) == 1
    import_space(space, actor=ingest._this_actor())

    logs = {p.stem for p in (space / ".datacore" / "events").glob("*.jsonl")}
    assert logs == {"testhost"}, f"wrote {logs}, must be the host's own log"


def test_two_unsynced_hosts_admitting_the_same_task_cannot_collide(tmp_path):
    """The fork this ends.

    Modelled as two SEPARATE checkouts, because that is the real situation:
    each host imports from its own copy before either has seen the other's
    events, so idempotence cannot help — `scan()` on host B has no knowledge
    of host A's import. Their logs then meet in a git merge.

    With a shared `genesis` actor both sides write genesis.jsonl from their
    own counters and claim the same seq. With per-host actors the logs are
    different files and the union is simply both.
    """
    import json

    from ledger.genesis import import_space

    logs = {}
    for host in ("hosta", "hostb"):
        s = tmp_path / host / "0-testspace"
        (s / "org").mkdir(parents=True)
        (s / "org" / "next_actions.org").write_text(ORG, encoding="utf-8")
        import_space(s, actor=host)
        for f in (s / ".datacore" / "events").glob("*.jsonl"):
            logs[f.name] = logs.get(f.name, "") + f.read_text()

    assert set(logs) == {"hosta.jsonl", "hostb.jsonl"}, (
        f"both hosts must write their own file, got {set(logs)}"
    )

    seen = {}
    for text in logs.values():
        for line in text.splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            key = (e.get("actor"), e.get("seq"))
            assert key not in seen, f"two events claim {key} — a fork"
            seen[key] = line


def test_sweep_does_not_fall_back_to_the_shared_genesis_actor():
    """Pin the call site: a default-actor import_space() is the bug."""
    src = (LIB / "ledger_ingest_org.py").read_text()
    assert "import_space(space, actor=_this_actor())" in src
    assert "import_space(space)\n" not in src, (
        "a bare import_space(space) defaults to actor='genesis', which every "
        "host would then write to"
    )
