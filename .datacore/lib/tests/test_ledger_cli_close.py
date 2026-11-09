"""`ledger_cli.py close` closes a task in the ledger without its org projection (2026-10-29).

WHY. Closing a task normally means editing its state in the space's generated
org/next_actions.org; the hourly ingest turns that edit into an `item.dismiss`.
When a space's ingest is stopped, the projection is stale or missing and the
task tool cannot find the item. On 2026-10-29 that blocked 25 closes in 5-plur
and they had to be written by hand. This is the same conditional dismissal the
ingest writes, through the write guard, as one command.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_ledger_cli import run_cli  # noqa: E402

LIB = Path(__file__).resolve().parents[1]


def _space(tmp_path):
    space = tmp_path / "space"
    space.mkdir()
    r = run_cli("append", "--space", str(space), "--type", "item.create",
                "--payload", json.dumps({"id": "t1", "title": "Continue: old plan", "state": "TODO"}),
                tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    (space / ".datacore" / "ledger-edit-protocol").write_text("2\n")   # as in every live space
    return space


def _item(space, item_id):
    sys.path.insert(0, str(LIB))
    from ledger.fold import fold
    from ledger.log import read_events
    return fold(read_events(space)).items[item_id]


def _lines(space):
    return sum(len(p.read_text().splitlines()) for p in (space / ".datacore" / "events").glob("*.jsonl"))


def test_close_dismisses_with_kind_and_reason(tmp_path):
    space = _space(tmp_path)
    r = run_cli("close", "--space", str(space), "--id", "t1", "--kind", "dropped",
                "--reason", "superseded by the new plan", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    item = _item(space, "t1")
    assert item.status == "dismissed"
    assert item.closed_kind == "dropped"
    assert "superseded by the new plan" in (item.closed_reason or "")


def test_close_done(tmp_path):
    space = _space(tmp_path)
    r = run_cli("close", "--space", str(space), "--id", "t1", "--kind", "done",
                "--reason", "PR merged", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert _item(space, "t1").closed_kind == "done"


def test_unknown_or_closed_item_writes_nothing(tmp_path):
    space = _space(tmp_path)
    before = _lines(space)
    r = run_cli("close", "--space", str(space), "--id", "nope", "--kind", "done",
                "--reason", "x", tmp_path=tmp_path)
    assert r.returncode != 0 and _lines(space) == before
    assert run_cli("close", "--space", str(space), "--id", "t1", "--kind", "done",
                   "--reason", "x", tmp_path=tmp_path).returncode == 0
    after_first = _lines(space)
    r = run_cli("close", "--space", str(space), "--id", "t1", "--kind", "dropped",
                "--reason", "again", tmp_path=tmp_path)
    assert r.returncode != 0 and _lines(space) == after_first
