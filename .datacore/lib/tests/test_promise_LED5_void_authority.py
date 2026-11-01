"""LED-5: No one cancels a record any more -- not their own, not anyone's: a
new cancel (`ledger.void`) is refused at write time for every actor, however
authorised, and a mistake is corrected by a new forward event. In the HISTORY,
a cancel by the record's own writer or by someone not allowed still has no
effect and is flagged.

Rule: DIP-0034 amendment of 2026-10-31, items 2 and 3; owner approval of this
rewrite 2026-11-01. The promise until then read: "no one can cancel their own
bad record; a cancel from someone not allowed has no effect and is flagged".

Seeded failure: there is no authorisation rule for cancelling a record at all
(audit D T1/T7) -- no `ledger.void` type exists, and the out-of-band exception
file excuses whatever anyone with commit access writes into it. The agent that
forged an event could equally "cancel" it.

Second seeded failure (2026-11-01): after the rule changed, the event log
still wrote a new void for anyone, a human principal or a listed voider.

Promise, as evals, on the same hand-written forgery as LED-4:
  * a NEW void by any actor -- the writer itself, a human principal, an
    explicit voider, a stranger -- is refused before anything is written;
HISTORY (voids written before the retirement, by the legacy fixture helper
`tests/_legacy_void.py`), judged exactly as before:
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
from tests._legacy_void import append_legacy_void

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


def _payload(seq: int, h: str) -> dict:
    return {"log": "miles.jsonl", "seq": seq, "hash": h, "reason": "not mine to keep"}


def _void(space: Path, actor: str, seq: int, h: str):
    """A void written BEFORE the retirement (history), the way it was written then."""
    return append_legacy_void(EventLog(space, actor), _payload(seq, h))


@pytest.mark.parametrize("actor", ["miles", "gregor", "tris", "stranger"])
def test_a_new_void_is_refused_for_every_actor(tmp_path, monkeypatch, actor):
    _registry(tmp_path, monkeypatch, "voiders: [tris]\n")
    space, path, seq, h = _forged_space(tmp_path)
    before = {p.name: p.read_bytes() for p in sorted((space / ".datacore").rglob("*.jsonl"))}
    with pytest.raises(ValueError) as exc:
        EventLog(space, actor).append("ledger.void", _payload(seq, h))
    assert type(exc.value).__name__ == "VoidRetired", f"{actor}: {exc.value!r}"
    assert "ledger.compensate" in str(exc.value), exc.value
    after = {p.name: p.read_bytes() for p in sorted((space / ".datacore").rglob("*.jsonl"))}
    assert after == before, f"a void by {actor} was written"
    assert "forged" in fold(read_events(space)).items


def _space_errors(space: Path) -> list[str]:
    return [f"{p.name}: {e}" for p in sorted((space / ".datacore" / "events").glob("*.jsonl"))
            for e in verify_chain(p)]


def _void_reported(space: Path) -> bool:
    return any("void" in e.lower() for e in _space_errors(space))


def test_a_historic_self_void_has_no_effect_and_is_reported(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "miles", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)
    assert "forged" in fold(read_events(space)).items


def test_a_historic_void_by_an_unauthorised_agent_has_no_effect_and_is_reported(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "winston", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)
    assert "forged" in fold(read_events(space)).items


def test_a_historic_void_by_an_actor_outside_the_registry_has_no_effect(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "stranger", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)


def test_a_historic_void_by_an_explicit_voider_of_another_actors_event_keeps_its_effect(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, "voiders: [tris]\n")
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "tris", seq, h)
    assert _space_errors(space) == []
    assert "forged" not in fold(read_events(space)).items


def test_a_historic_void_by_an_explicit_voider_of_its_own_event_has_no_effect(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, "voiders: [miles]\n")
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "miles", seq, h)
    assert any("signature" in e for e in verify_chain(path))
    assert _void_reported(space)
