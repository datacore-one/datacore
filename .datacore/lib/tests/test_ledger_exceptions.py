"""A reviewed exception excuses one event exactly as written -- and nothing else.

An event written with a hash that never matched its body cannot be corrected on
an append-only log without re-sealing the tail, which would fork it from origin.
Meanwhile the checkpoint refused the whole space on the first mismatch, so that
space had NO restore point, forever -- the opposite of what the check is for.

The entry pins BOTH hashes, so it cannot be used to hide a later edit: change
the body and the computed hash moves, nothing matches, and verification fails.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

from ledger import exceptions  # noqa: E402

RECORDED = "0" * 63 + "1"
COMPUTED = "0" * 63 + "2"


def _root(tmp_path, body: str) -> Path:
    d = tmp_path / ".datacore" / "registry"
    d.mkdir(parents=True)
    (d / "ledger-exceptions.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def _one(tmp_path, **over) -> Path:
    e = {"space": "5-fixture", "log": "tris.jsonl", "seq": 5,
         "recorded": RECORDED, "computed": COMPUTED}
    e.update(over)
    body = ("version: 1\n"
            "malformed_hash:\n"
            f"  - space: {e['space']}\n"
            f"    log: {e['log']}\n"
            f"    seq: {e['seq']}\n"
            f"    recorded: {e['recorded']}\n"
            f"    computed: {e['computed']}\n")
    return _root(tmp_path, body)


def test_the_named_event_is_excused(tmp_path):
    root = _one(tmp_path)
    assert exceptions.is_recorded("5-fixture", "tris.jsonl", 5, RECORDED, COMPUTED, root)


@pytest.mark.parametrize("field,value", [
    ("space", "0-other"), ("log", "mac.jsonl"), ("seq", 6),
])
def test_a_different_event_is_not(tmp_path, field, value):
    root = _one(tmp_path)
    args = {"space": "5-fixture", "log": "tris.jsonl", "seq": 5}
    args[field] = value
    assert not exceptions.is_recorded(args["space"], args["log"], args["seq"],
                                      RECORDED, COMPUTED, root)


def test_editing_the_body_revokes_the_exception(tmp_path):
    """THE POINT: `computed` moves with the body, so this cannot hide an edit."""
    root = _one(tmp_path)
    assert not exceptions.is_recorded("5-fixture", "tris.jsonl", 5, RECORDED,
                                      "0" * 63 + "9", root)


def test_a_replaced_stored_hash_is_not_excused(tmp_path):
    root = _one(tmp_path)
    assert not exceptions.is_recorded("5-fixture", "tris.jsonl", 5, "0" * 63 + "8",
                                      COMPUTED, root)


@pytest.mark.parametrize("body", [
    "", "not a mapping", "version: 1\n", "version: 1\nmalformed_hash: []\n",
    "version: 1\nmalformed_hash:\n  - space: 5-fixture\n",     # incomplete entry
])
def test_absent_or_useless_registries_excuse_nothing(tmp_path, body):
    root = _root(tmp_path, body)
    assert exceptions.load(root) == set()
    assert not exceptions.is_recorded("5-fixture", "tris.jsonl", 5, RECORDED, COMPUTED, root)


def test_a_missing_registry_fails_closed(tmp_path):
    assert exceptions.load(tmp_path) == set()


def test_an_entry_claiming_the_hashes_are_equal_is_refused(tmp_path):
    """Equal hashes are not a mismatch; such an entry could only be noise."""
    root = _one(tmp_path, computed=RECORDED)
    assert exceptions.load(root) == set()
