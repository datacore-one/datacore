"""LED-5: No one can cancel their own bad record; a cancel from someone not
allowed has no effect and is flagged.

Seeded failure: there is no authorisation rule for cancelling a record at all
(audit D T1/T7) -- no `ledger.void` type exists, and the out-of-band exception
file excuses whatever anyone with commit access writes into it. The agent that
forged an event could equally "cancel" it.

Promise, as evals, on the same hand-written forgery as LED-4:
  * a void appended by the forged event's own actor -> verify still fails, and
    the void is reported as having no effect;
  * a void by an agent that is not an authorised voider -> still fails, reported;
  * an agent named in the registry's explicit `voiders:` list may void another
    actor's event, but never its own;
  * fold keeps an event whose only void is unauthorised.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import actor_identity
from ledger.events import body_dict, compute_hash
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.verify import verify_chain

PRINCIPALS = """\
principals:
  gregor:
    kind: human
    writes_as: [gregor]
  miles:
    kind: agent
    writes_as: [miles]
  winston:
    kind: agent
    writes_as: [winston]
  tris:
    kind: agent
    writes_as: [tris]
"""


def _registry(tmp_path, monkeypatch, extra: str = "") -> None:
    p = tmp_path / "principals.yaml"
    p.write_text(PRINCIPALS + extra)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)


def _forged_space(tmp_path: Path):
    space = tmp_path / "6-fixture"
    miles = EventLog(space, "miles")
    miles.append("item.create", {"id": "t1", "title": "real", "state": "NEXT"})
    path = space / ".datacore" / "events" / "miles.jsonl"
    last = json.loads(path.read_text().splitlines()[-1])
    body = body_dict(last["seq"] + 1, f"{int(last['hlc'].split('.')[0]) + 1000}.0000.miles", "miles",
                     "item.create", {"id": "forged", "title": "forged", "state": "DONE"}, last["hash"])
    h = compute_hash(body)
    with path.open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": h}) + "\n")
    miles.append("item.create", {"id": "t2", "title": "later", "state": "NEXT"})
    return space, path, body["seq"], h


def _void(space: Path, actor: str, seq: int, h: str):
    return EventLog(space, actor).append("ledger.void", {
        "log": "miles.jsonl", "seq": seq, "hash": h, "reason": "not mine to keep"})


def _space_errors(space: Path) -> list[str]:
    return [f"{p.name}: {e}" for p in sorted((space / ".datacore" / "events").glob("*.jsonl"))
            for e in verify_chain(p)]


def _void_reported(space: Path) -> bool:
    return any("void" in e.lower() for e in _space_errors(space))


def test_a_self_void_has_no_effect_and_is_reported(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "miles", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)
    assert "forged" in fold(read_events(space)).items


def test_a_void_by_an_unauthorised_agent_has_no_effect_and_is_reported(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "winston", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)
    assert "forged" in fold(read_events(space)).items


def test_an_actor_outside_the_registry_cannot_void(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "stranger", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)


def test_an_explicit_voider_agent_may_void_another_actors_event(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, "voiders: [tris]\n")
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "tris", seq, h)
    assert _space_errors(space) == []
    assert "forged" not in fold(read_events(space)).items


def test_an_explicit_voider_still_cannot_void_its_own_event(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, "voiders: [miles]\n")
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "miles", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)
