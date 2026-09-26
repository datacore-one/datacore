"""LED-4 (retirement): A bad record is cancelled only by a new, authorised
cancelling record inside the history, never by a side list of excuses.

Seeded failure: `registry/ledger-exceptions.yaml` is still consulted. An entry
there (`invalid_signature` or `malformed_hash`) makes `ledger_cli.py verify`
pass a forged event that no void cancels, the CLI advertises "reviewed
exception(s)", and key collection (`ledger_keys_collect.signatures`) skips
events by consulting the side list instead of the ledger's own voids.

Promise, as evals: with a side-list entry naming the forged event and no void,
verify FAILS; with an authorised void, verify passes and reports "1 voided
record(s)"; key evidence skips an event only when an in-ledger void cancels it.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import actor_identity
from ledger.events import body_dict, compute_hash
from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[1]
PRINCIPALS = """\
principals:
  gregor:
    kind: human
    writes_as: [gregor]
  miles:
    kind: agent
    writes_as: [miles]
"""


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    reg = root / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "principals.yaml").write_text(PRINCIPALS)
    return root


def _forged(root: Path, *, bad_hash: bool = False):
    space = root / "6-fixture"
    miles = EventLog(space, "miles")
    miles.append("item.create", {"id": "t1", "title": "real", "state": "NEXT"})
    path = space / ".datacore" / "events" / "miles.jsonl"
    last = json.loads(path.read_text().splitlines()[-1])
    body = body_dict(last["seq"] + 1, f"{int(last['hlc'].split('.')[0]) + 1000}.0000.miles", "miles",
                     "metric.attest", {"metric": "cadence.run", "result": "ok"}, last["hash"])
    computed = compute_hash(body)
    stored = "0" * 63 + "1" if bad_hash else computed
    with path.open("a") as f:
        f.write(json.dumps({**body, "hash": stored, "sig": stored}) + "\n")
    miles.append("item.create", {"id": "t2", "title": "later", "state": "NEXT"})
    return space, body["seq"], stored, computed


def _side_list(root: Path, seq: int, stored: str, computed: str) -> None:
    (root / ".datacore" / "registry" / "ledger-exceptions.yaml").write_text(
        "version: 1\n"
        "malformed_hash:\n"
        f"  - space: 6-fixture\n    log: miles.jsonl\n    seq: {seq}\n"
        f"    recorded: {stored}\n    computed: {computed}\n"
        "invalid_signature:\n"
        f"  - space: 6-fixture\n    log: miles.jsonl\n    seq: {seq}\n    hash: {stored}\n"
        f"    sig_sha256: {hashlib.sha256(stored.encode()).hexdigest()}\n"
        "    disposition: void\n    reason: side list\n")


def _cli(root: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATACORE_ROOT": str(root), "DATACORE_ACTOR": "gregor"}
    return subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), *argv],
                          capture_output=True, text=True, env=env)


def test_a_side_list_entry_does_not_excuse_a_forged_signature(tmp_path):
    root = _root(tmp_path)
    space, seq, stored, computed = _forged(root)
    _side_list(root, seq, stored, computed)
    r = _cli(root, "verify", "--space", str(space))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "reviewed exception" not in r.stdout


def test_a_side_list_entry_does_not_excuse_a_hash_mismatch(tmp_path):
    root = _root(tmp_path)
    space, seq, stored, computed = _forged(root, bad_hash=True)
    _side_list(root, seq, stored, computed)
    r = _cli(root, "verify", "--space", str(space))
    assert r.returncode == 1, r.stdout + r.stderr


def test_verify_names_the_voided_records(tmp_path):
    root = _root(tmp_path)
    space, seq, _, _ = _forged(root)
    v = _cli(root, "void", "--space", str(space), "--log", "miles.jsonl", "--seq", str(seq),
             "--reason", "hand-written")
    assert v.returncode == 0, v.stdout + v.stderr
    r = _cli(root, "verify", "--space", str(space))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 voided record(s)" in r.stdout


def _signatures(root: Path, monkeypatch):
    monkeypatch.setattr(actor_identity, "PRINCIPALS", root / ".datacore" / "registry" / "principals.yaml")
    import ledger_keys_collect
    return ledger_keys_collect.signatures(root)


def test_key_evidence_skips_an_event_voided_in_the_ledger(tmp_path, monkeypatch):
    root = _root(tmp_path)
    space, seq, _, _ = _forged(root)
    EventLog(space, "gregor").append("ledger.void", {
        "log": "miles.jsonl", "seq": seq, "hash": _forged_hash(space, seq), "reason": "hand-written"})
    sigs = _signatures(root, monkeypatch)
    assert all(body["seq"] != seq for body, _ in sigs.get("miles", []))


def test_key_evidence_ignores_the_side_list(tmp_path, monkeypatch):
    root = _root(tmp_path)
    space, seq, stored, computed = _forged(root)
    _side_list(root, seq, stored, computed)
    sigs = _signatures(root, monkeypatch)
    assert any(body["seq"] == seq for body, _ in sigs.get("miles", []))


def _forged_hash(space: Path, seq: int) -> str:
    for line in (space / ".datacore" / "events" / "miles.jsonl").read_text().splitlines():
        ev = json.loads(line)
        if ev["seq"] == seq:
            return ev["hash"]
    raise AssertionError("no such event")
