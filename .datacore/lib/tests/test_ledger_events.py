"""Tests for ledger.events - canonical bytes, hash chain."""

from ledger.events import body_dict, canonical_bytes, compute_hash, to_line, from_line, Event, EVENT_TYPES


def test_canonical_bytes_stable_under_key_order():
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})


def test_hash_changes_with_prev_and_payload():
    b1 = body_dict(0, "x", "mac", "item.create", {"id": "t1"}, "GENESIS")
    b2 = dict(b1, prev="other")
    b3 = body_dict(0, "x", "mac", "item.create", {"id": "t2"}, "GENESIS")
    assert compute_hash(b1) not in (compute_hash(b2), compute_hash(b3))


def test_line_roundtrip():
    e = Event(0, "s", "mac", "item.create", {"id": "t1"}, "GENESIS", "h", "sig")
    assert from_line(to_line(e)) == e


def test_event_types_frozen():
    assert "item.claim" in EVENT_TYPES and isinstance(EVENT_TYPES, frozenset)
    assert "approval.grant" in EVENT_TYPES
