"""P0-V (ledger-upgrade Phase 0, owner decision 6): a bad event is cancelled
only by an authorised, append-only void record inside the ledger. There is no
exceptions file and no code that reads one; the events the old file named are
voided in-ledger; history is never rewritten.

Kinds: deterministic (repository contents, the checkpoint restore path) and
production (the live voids are read, never written).

The behaviour of the void itself -- verify refuses a forged event no void
cancels, accepts it once an authorised void does, and an actor can never void
its own events -- is pinned by the promise evals LED-4 and LED-5
(test_promise_LED4_void_record.py, test_promise_LED4_no_side_list.py,
test_promise_LED5_void_authority.py). This file pins what decision 6 adds:
the side list and its code are gone, not just ignored.

Seeded failure: `registry/ledger-exceptions.yaml` and `ledger/exceptions.py`
still present (the state on 2026-10-04: the file was "excused nothing" but
still shipped, and ledger_checkpoint still imported the shim).
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

LIB = Path(__file__).resolve().parents[2]
DATACORE = LIB.parent
ROOT = DATACORE.parent

# The three events the retired exceptions file named (decision 6 names them:
# two in the trading space, one in the PLUR space). Identified by space,
# log, seq and the hash stored in the file.
FORMERLY_EXCEPTED = [
    ("6-meridian", "miles", 21140, "dabfeaef2c304d17b65025754f0235c35c8b5d09e66e92cf0ce193e488805fc6"),
    ("6-meridian", "miles", 21141, "6b84718b6f2d64ba69759acbbc89843ce204ac1aed68e8a956e7ee8572394aa9"),
    ("5-plur", "tris", 5, "008175039b02ab6e34fe4e77a96613b4e31564f3a3948dd115cab1265f2a803c"),
]


def test_no_exceptions_file_ships():
    path = DATACORE / "registry" / "ledger-exceptions.yaml"
    assert not path.exists(), (
        f"{path.relative_to(ROOT)} still exists. Owner decision 6: the out-of-band exceptions "
        "file is removed once the in-ledger void exists; a side list anyone can extend is "
        "exactly what the decision forbids.")


def test_no_code_reads_an_exceptions_list():
    shim = LIB / "ledger" / "exceptions.py"
    assert not shim.exists(), (
        f"{shim.relative_to(ROOT)} still exists: the exceptions module is retired code "
        "(decision 6 says the file AND its code are removed).")
    importers = []
    for py in sorted(LIB.rglob("*.py")):
        if "tests" in py.relative_to(LIB).parts or "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n in ("ledger.exceptions", ".exceptions", "exceptions")
                   or n.endswith("ledger.exceptions") for n in names) and \
                    ("ledger" in py.parts or any("ledger" in n for n in names)):
                importers.append(str(py.relative_to(ROOT)))
        if "ledger-exceptions.yaml" in py.read_text(encoding="utf-8", errors="replace") \
                and py.name != "voids.py":
            importers.append(f"{py.relative_to(ROOT)} (names the file)")
    assert not importers, ("production code still reads an exceptions list: "
                           + ", ".join(sorted(set(importers))))


def test_checkpoint_restore_excuses_a_mismatch_only_through_a_void(tmp_path, monkeypatch):
    """The checkpoint restore path (the last consumer of the shim) accepts a
    stored-hash mismatch only when an authorised in-ledger void names it."""
    import actor_identity
    import ledger_checkpoint as C
    from ledger.events import body_dict, compute_hash
    from ledger.log import EventLog

    reg = tmp_path / "principals.yaml"
    reg.write_text("principals:\n  gregor:\n    kind: human\n    writes_as: [gregor]\n"
                   "  miles:\n    kind: agent\n    writes_as: [miles]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    space = tmp_path / "6-fixture"
    miles = EventLog(space, "miles")
    miles.append("item.create", {"id": "t1", "title": "real", "state": "NEXT"})
    path = space / ".datacore" / "events" / "miles.jsonl"
    last = json.loads(path.read_text().splitlines()[-1])
    body = body_dict(last["seq"] + 1, f"{int(last['hlc'].split('.')[0]) + 1000}.0000.miles", "miles",
                     "metric.attest", {"metric": "cadence.run", "result": "ok"}, last["hash"])
    bad = "0" * 63 + "1"
    with path.open("a") as f:
        f.write(json.dumps({**body, "hash": bad, "sig": ""}, sort_keys=True, separators=(",", ":")) + "\n")

    def document():
        chains = {p.name: p.read_text() for p in sorted((space / ".datacore" / "events").glob("*.jsonl"))}
        return {"version": 1, "chains": chains, "state_root": "x", "org_sha256": "y"}

    import pytest
    with pytest.raises(ValueError, match="integrity"):
        C._restore(document(), space.name)

    gregor = EventLog(space, "gregor")
    gregor.append("ledger.void", {"log": "miles.jsonl", "seq": body["seq"], "hash": bad,
                                  "body_sha256": compute_hash(body), "reason": "test"})
    with pytest.raises(C.StateRootMismatch):
        # Past the integrity check: the void cancels exactly that mismatch.
        C._restore(document(), space.name)


def test_the_formerly_excepted_events_are_voided_in_ledger():
    """Production, read-only: each event the retired file named is cancelled
    by an authorised void in its own space's ledger. Could not tell is red."""
    from ledger.voids import for_events_dir
    missing = []
    for space, log, seq, stored in FORMERLY_EXCEPTED:
        events = ROOT / space / ".datacore" / "events"
        assert events.is_dir(), f"could not tell: {events} is not readable here"
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in events.glob("*.jsonl")}
        voids = for_events_dir(events)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in events.glob("*.jsonl")}
        assert before == after, f"reading the voids changed {space}'s ledger"
        if (log, seq, stored) not in voids.effective:
            missing.append(f"{space} {log}.jsonl seq {seq}")
    assert not missing, "not cancelled by an authorised in-ledger void: " + ", ".join(missing)
