"""Ledger-event subscribers (DIP-0024 amendment 2026-11-09).

At least once, in log order per writer, a cursor per subscriber/space/writer,
a failed call recorded and retried, an undeclared name refused, and a new
subscriber starting at the end of each log rather than replaying history (a
fresh request reporter must not send the owner two thousand old releases).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_subscribers as ls  # noqa: E402


def ev(actor, seq, type_, **payload):
    return SimpleNamespace(actor=actor, seq=seq, type=type_, payload=payload,
                           hash=f"h-{actor}-{seq}", hlc=f"{1794000000000 + seq}.0000.{actor}")


class World:
    """One space whose events a test appends to between runs."""

    def __init__(self):
        self.events: list = []

    def read(self, space):
        return list(self.events)


SUB = {"name": "watch", "types": ["item.release"], "call": "x:y", "declared_by": "test"}


def run(world, tmp_path, handler, subs=(SUB,), name=None):
    return ls.run(spaces=[Path("/s/2-datacore")], subscribers=list(subs),
                  state_dir=tmp_path, read_events=world.read,
                  resolve=lambda call: handler, only=name)


def test_a_new_subscriber_starts_at_the_end_of_each_log(tmp_path):
    w = World()
    w.events = [ev("miles", 1, "item.release", id="old")]
    got = []
    run(w, tmp_path, got.append)
    assert got == []


def test_a_new_event_is_delivered_once_with_its_alias(tmp_path):
    w = World()
    w.events = [ev("miles", 1, "item.create", id="a")]
    got = []
    run(w, tmp_path, got.append)
    w.events.append(ev("miles", 2, "item.release", id="a", error="check failed"))
    run(w, tmp_path, got.append)
    run(w, tmp_path, got.append)
    assert len(got) == 1
    e = got[0]
    assert (e["type"], e["alias"], e["space"], e["actor"], e["seq"]) == \
        ("item.release", "agent.run.failed", "2-datacore", "miles", 2)
    assert e["payload"]["id"] == "a"


def test_only_the_declared_types_are_delivered(tmp_path):
    w = World()
    w.events = [ev("miles", 1, "item.create", id="a")]
    got = []
    run(w, tmp_path, got.append)
    w.events += [ev("miles", 2, "item.claim", id="a"), ev("miles", 3, "item.release", id="a")]
    run(w, tmp_path, got.append)
    assert [e["seq"] for e in got] == [3]


def test_a_failed_call_is_recorded_and_retried(tmp_path):
    w = World()
    w.events = [ev("miles", 1, "item.create", id="a")]
    run(w, tmp_path, lambda e: None)
    w.events.append(ev("miles", 2, "item.release", id="a"))

    def boom(e):
        raise RuntimeError("telegram down")

    out = run(w, tmp_path, boom)
    assert out["watch"]["failed"] == 1
    st = ls.status(state_dir=tmp_path)
    assert st["watch"]["failures"][0]["error"].endswith("telegram down")

    got = []
    out = run(w, tmp_path, got.append)
    assert [e["seq"] for e in got] == [2]
    assert out["watch"]["failed"] == 0
    assert ls.status(state_dir=tmp_path)["watch"]["failures"] == []


def test_a_failure_holds_back_later_events_of_that_log_only(tmp_path):
    w = World()
    w.events = [ev("miles", 1, "item.create"), ev("tris", 1, "item.create")]
    run(w, tmp_path, lambda e: None)
    w.events += [ev("miles", 2, "item.release", id="m2"), ev("miles", 3, "item.release", id="m3"),
                 ev("tris", 2, "item.release", id="t2")]
    seen = []

    def picky(e):
        seen.append(e["payload"]["id"])
        if e["payload"]["id"] == "m2":
            raise RuntimeError("no")

    run(w, tmp_path, picky)
    assert "m3" not in seen and "t2" in seen


def test_a_writer_that_appears_later_is_read_from_its_first_event(tmp_path):
    w = World()
    w.events = [ev("miles", 1, "item.create")]
    run(w, tmp_path, lambda e: None)
    w.events += [ev("data", 0, "item.release", id="d0")]
    got = []
    run(w, tmp_path, got.append)
    assert [e["payload"]["id"] for e in got] == ["d0"]


def test_an_undeclared_subscriber_is_refused(tmp_path):
    with pytest.raises(ls.UnknownSubscriber):
        run(World(), tmp_path, lambda e: None, name="nobody")


def test_the_registry_subscribers_resolve_to_callables():
    import yaml
    reg = yaml.safe_load((LIB.parent / "registry" / "hooks.yaml").read_text())
    for s in reg.get("ledger_events") or []:
        assert callable(ls.resolve_call(s["call"])), s["call"]
