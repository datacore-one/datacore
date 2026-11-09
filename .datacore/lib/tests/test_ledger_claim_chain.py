"""A completed item may create the follow-up it carries in `then`."""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import ledger_claim as lc  # noqa: E402


def test_no_then_means_nothing(tmp_path):
    assert lc.chain_follow_up(tmp_path, "miles", {"id": "a"}) == (False, "")


def test_an_incomplete_then_is_named_not_written(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(lc, "guarded_append", lambda log, kind, payload: written.append(payload))
    ok, note = lc.chain_follow_up(tmp_path, "miles", {"id": "a", "then": {"id": "a-verify", "title": "t"}})
    assert not ok and "lacks assignee, check" in note and written == []


def test_the_follow_up_is_written_under_the_completing_actor(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(lc, "guarded_append", lambda log, kind, payload: written.append((kind, payload)))
    then = {"id": "a-verify", "title": "Pull and verify", "assignee": "winston", "check": "true"}
    ok, note = lc.chain_follow_up(tmp_path, "miles", {"id": "a", "then": then})
    assert ok and note.startswith("CHAINED  a-verify -> winston")
    kind, payload = written[0]
    assert kind == "item.create" and payload["requested_by"] == "miles" and payload["after"] == "a"
    assert payload["assignee"] == "winston" and payload["check"] == "true"
