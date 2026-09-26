"""LED-4: A bad record is cancelled only by a new, authorised cancelling record
inside the history, never by a side list of excuses.

Seeded failure: the 2026-09-25 forgery in 6-meridian -- an agent appended an
event by hand with `sig` = the event's own hash (non-canonical JSON, bypassing
EventLog), and genuine events then chained on top. Today the only way to clear
it is an entry in the out-of-band `registry/ledger-exceptions.yaml`; there is
no `ledger.void` event type, so a cancelling record cannot even be written, and
verify keeps failing forever.

Promise, as evals:
  * unvoided -> verify fails;
  * voided by a human principal via a `ledger.void` record {log, seq, hash,
    reason} in the same space -> verify passes and fold ignores the event;
  * editing the voided event afterwards -> verify fails again;
  * `ledger_cli.py void` writes such a record with the current actor, and it
    also cancels an event whose stored hash never matched its body (the
    5-plur tris.jsonl seq 5 shape).
"""
from __future__ import annotations

import json
import sys
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
"""


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    p = tmp_path / "principals.yaml"
    p.write_text(PRINCIPALS)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    return p


def _hand_append(path: Path, type_: str, payload: dict, *, bad_hash: bool = False) -> tuple[int, str]:
    """Append one line the way the agent did: json.dumps, sig = hash, no EventLog."""
    last = json.loads(path.read_text().splitlines()[-1])
    seq = last["seq"] + 1
    body = body_dict(seq, f"{int(last['hlc'].split('.')[0]) + 1000}.0000.miles", "miles",
                     type_, payload, last["hash"])
    h = compute_hash(body)
    if bad_hash:
        h = "0" * 63 + "1"
    with path.open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": h}) + "\n")
    return seq, h


def _forged_space(tmp_path: Path, *, bad_hash: bool = False):
    space = tmp_path / "6-fixture"
    miles = EventLog(space, "miles")
    miles.append("item.create", {"id": "t1", "title": "real", "state": "NEXT"})
    path = space / ".datacore" / "events" / "miles.jsonl"
    seq, h = _hand_append(path, "item.create", {"id": "forged", "title": "forged", "state": "DONE"},
                          bad_hash=bad_hash)
    # Genuine events chain on top, as they did in 6-meridian.
    miles.append("item.create", {"id": "t2", "title": "later", "state": "NEXT"})
    return space, path, seq, h


def _space_errors(space: Path) -> list[str]:
    return [f"{p.name}: {e}" for p in sorted((space / ".datacore" / "events").glob("*.jsonl"))
            for e in verify_chain(p)]


def _void(space: Path, actor: str, seq: int, h: str, log: str = "miles.jsonl"):
    return EventLog(space, actor).append("ledger.void", {
        "log": log, "seq": seq, "hash": h, "reason": "hand-written, not produced by EventLog"})


def test_an_unvoided_forgery_fails_verify(tmp_path):
    space, path, _, _ = _forged_space(tmp_path)
    assert verify_chain(path), "the forged event must fail verification"


def test_a_void_by_a_human_principal_cancels_it(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "gregor", seq, h)
    assert _space_errors(space) == []


def test_fold_ignores_the_voided_event(tmp_path):
    space, _, seq, h = _forged_space(tmp_path)
    assert "forged" in fold(read_events(space)).items
    _void(space, "gregor", seq, h)
    items = fold(read_events(space)).items
    assert "forged" not in items
    assert {"t1", "t2"} <= set(items)


def test_editing_the_voided_event_fails_verify_again(tmp_path):
    """Re-hash an edited body (hash = sig = new hash): the void no longer names it."""
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "gregor", seq, h)
    lines = path.read_text().splitlines()
    idx = next(i for i, line in enumerate(lines) if json.loads(line)["seq"] == seq)
    ev = json.loads(lines[idx])
    ev["payload"]["title"] = "edited"
    body = body_dict(ev["seq"], ev["hlc"], ev["actor"], ev["type"], ev["payload"], ev["prev"])
    ev["hash"] = ev["sig"] = compute_hash(body)
    lines[idx] = json.dumps(ev)
    path.write_text("\n".join(lines) + "\n")
    assert verify_chain(path)


def test_editing_the_voided_body_but_keeping_its_hash_fails_verify(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(space, "gregor", seq, h)
    lines = path.read_text().splitlines()
    idx = next(i for i, line in enumerate(lines) if json.loads(line)["seq"] == seq)
    ev = json.loads(lines[idx])
    ev["payload"]["title"] = "edited"
    lines[idx] = json.dumps(ev)
    path.write_text("\n".join(lines) + "\n")
    assert verify_chain(path)


def test_a_void_naming_a_different_hash_does_not_cancel(tmp_path):
    space, path, seq, _ = _forged_space(tmp_path)
    _void(space, "gregor", seq, "f" * 64)
    assert verify_chain(path)


def test_a_void_in_another_space_does_not_cancel(tmp_path):
    space, path, seq, h = _forged_space(tmp_path)
    _void(tmp_path / "0-other", "gregor", seq, h)
    assert verify_chain(path)


def _cli(monkeypatch, *argv):
    import ledger_cli
    monkeypatch.setattr(sys, "argv", ["ledger_cli.py", *argv])
    ledger_cli.main()


def test_cli_void_appends_a_cancelling_record_as_the_current_actor(tmp_path, monkeypatch):
    space, path, seq, h = _forged_space(tmp_path)
    monkeypatch.setenv("DATACORE_ACTOR", "gregor")
    _cli(monkeypatch, "void", "--space", str(space), "--log", "miles.jsonl",
         "--seq", str(seq), "--reason", "hand-written by an agent")
    voider = space / ".datacore" / "events" / "gregor.jsonl"
    ev = json.loads(voider.read_text().splitlines()[-1])
    assert ev["type"] == "ledger.void" and ev["actor"] == "gregor"
    assert ev["payload"]["seq"] == seq and ev["payload"]["hash"] == h
    assert _space_errors(space) == []


def test_cli_void_cancels_an_event_whose_hash_never_matched(tmp_path, monkeypatch):
    space, path, seq, h = _forged_space(tmp_path, bad_hash=True)
    assert any("hash mismatch" in e for e in verify_chain(path))
    monkeypatch.setenv("DATACORE_ACTOR", "gregor")
    _cli(monkeypatch, "void", "--space", str(space), "--log", "miles.jsonl",
         "--seq", str(seq), "--reason", "hash never matched its body")
    assert _space_errors(space) == []
    assert "forged" not in fold(read_events(space)).items
