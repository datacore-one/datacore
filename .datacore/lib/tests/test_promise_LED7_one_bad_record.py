"""LED-7: One bad record or one wrong clock never stops the rest of the space
from recording, reading or syncing; it is flagged instead.

Seeded failures:
  * one malformed line that is not the last line of one writer's log makes
    `read_events` raise `CorruptLogError` for the WHOLE space, so fold,
    projection, checkpoint and policy stop for every writer (audit A#15, D8);
  * one event whose HLC is far in the future becomes every other writer's
    causal floor: their appends keep its physical time and bump the 4-digit
    counter, and the 10th append raises "HLC counter overflow" -- every writer
    in the space stalls (audit D7). `verify_chain` reports such an event as
    clean.

Promise, as evals: the reader flags the damaged log and withholds only that
log's tail from the bad line on; other writers' appends ignore a floor more
than ten minutes ahead of the clock and keep stamping near real time; verify
flags the future HLC.
"""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

from ledger.events import Event, body_dict, compute_hash, to_line
from ledger.fold import fold
from ledger.hlc import parse
from ledger.log import EventLog, read_events
from ledger.verify import verify_chain


def _space(tmp_path: Path) -> Path:
    space = tmp_path / "7-fixture"
    alice, bob = EventLog(space, "alice"), EventLog(space, "bob")
    alice.append("item.create", {"id": "a1", "title": "a1", "state": "NEXT"})
    alice.append("item.create", {"id": "a2", "title": "a2", "state": "NEXT"})
    alice.append("item.create", {"id": "a3", "title": "a3", "state": "NEXT"})
    bob.append("item.create", {"id": "b1", "title": "b1", "state": "NEXT"})
    return space


def _corrupt_middle(space: Path) -> Path:
    path = space / ".datacore" / "events" / "alice.jsonl"
    lines = path.read_text().splitlines()
    lines.insert(1, "{this is not json")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_one_malformed_line_does_not_stop_readers_of_the_space(tmp_path):
    space = _space(tmp_path)
    _corrupt_middle(space)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        events = read_events(space)
    ids = {e.payload.get("id") for e in events}
    assert "b1" in ids, "another writer's events must still be read"
    assert "a1" in ids, "the damaged log's events before the bad line are kept"
    assert not ({"a2", "a3"} & ids), "the damaged log's tail after the bad line is withheld"
    assert any("alice.jsonl" in str(w.message) for w in caught), "the damage must be flagged"


def test_fold_runs_over_a_space_with_one_damaged_log(tmp_path):
    space = _space(tmp_path)
    _corrupt_middle(space)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        items = fold(read_events(space)).items
    assert {"a1", "b1"} <= set(items)


def test_another_writer_can_still_append(tmp_path):
    space = _space(tmp_path)
    _corrupt_middle(space)
    e = EventLog(space, "bob").append("item.create", {"id": "b2", "title": "b2", "state": "NEXT"})
    assert e.seq == 1


def _future_event(space: Path, actor: str = "clockbad") -> str:
    year_2100 = 4102444800000
    path = space / ".datacore" / "events" / f"{actor}.jsonl"
    body = body_dict(0, f"{year_2100}.9990.{actor}", actor, "item.create",
                     {"id": "future", "title": "wrong clock", "state": "NEXT"}, "GENESIS")
    path.write_text(to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n")
    return str(path)


def test_a_future_hlc_does_not_stall_other_writers(tmp_path):
    space = _space(tmp_path)
    _future_event(space)
    bob = EventLog(space, "bob")
    now_ms = int(time.time() * 1000)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for i in range(20):
            e = bob.append("item.create", {"id": f"x{i}", "title": "x", "state": "NEXT"})
    pt, _, _ = parse(e.hlc)
    assert pt < now_ms + 10 * 60 * 1000, "the poisoned floor must not become bob's clock"


def test_verify_flags_a_future_hlc(tmp_path):
    space = _space(tmp_path)
    path = _future_event(space)
    errors = verify_chain(Path(path))
    assert any("future" in e.lower() for e in errors), errors
