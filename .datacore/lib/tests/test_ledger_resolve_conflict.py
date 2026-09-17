"""A retained edit conflict has an exit, whatever state its item is in.

`project` refuses a whole space while ANY item holds one, so an item with no
route out of a conflict stops that space's hourly cycle and every nightshift
run in it -- measured 2026-09-17, 5-plur, on a `completed` item.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_resolve_conflict as resolver  # noqa: E402
from ledger.edits import conditional_payload  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import project  # noqa: E402


def _space(tmp_path: Path, status: str) -> tuple[Path, str]:
    space = tmp_path / "9-fixture"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
    log = EventLog(space, "fixture")
    log.append("item.create", {"id": "t1", "title": "Ship it", "state": "NEXT", "space": space.name})
    if status in ("claimed", "completed", "dismissed"):
        log.append("item.claim", {"id": "t1", "executor": "fixture"})
    if status == "completed":
        log.append("item.complete", {"id": "t1", "decision": "approved"})
    if status == "dismissed":
        log.append("item.dismiss", {"id": "t1", "kind": "done", "reason": "fixture"})
    # A conditional edit built on a STALE base: the fold retains its refusal.
    item = fold(read_events(space)).items["t1"]
    stale = conditional_payload(item, {"title": "Ship it later"})
    stale["_merge"]["base"]["title"]["value"] = "something else entirely"
    log.append("item.update", stale)
    assert fold(read_events(space)).items["t1"].edit_conflicts
    with pytest.raises(Exception):
        project(fold(read_events(space)), space=space.name, as_of=0)
    return space, "t1"


@pytest.mark.parametrize("status", ["created", "claimed", "completed", "dismissed"])
def test_every_item_state_has_a_route_out_of_a_retained_conflict(tmp_path, status, monkeypatch):
    space, identity = _space(tmp_path, status)
    before = fold(read_events(space)).items[identity]

    assert resolver.main(["--space", str(space), "--item", identity, "--apply", "--actor", "fixture"]) == 0

    after = fold(read_events(space)).items[identity]
    assert after.edit_conflicts == {}, "the space must project again"
    assert after.status == before.status, "reconciling is not a lifecycle change"
    assert after.payload == before.payload, "reconciling changes no content"
    project(fold(read_events(space)), space=space.name, as_of=0)


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    space, identity = _space(tmp_path, "completed")
    events = len(read_events(space))
    assert resolver.main(["--space", str(space), "--item", identity]) == 0
    assert len(read_events(space)) == events
    assert "dry run" in capsys.readouterr().out


def test_all_reconciles_every_stuck_item_and_says_so_when_there_are_none(tmp_path, capsys):
    space, identity = _space(tmp_path, "completed")
    assert resolver.main(["--space", str(space), "--all", "--apply", "--actor", "fixture"]) == 0
    assert fold(read_events(space)).items[identity].edit_conflicts == {}
    assert resolver.main(["--space", str(space), "--all"]) == 0
    assert "nothing to reconcile" in capsys.readouterr().out
