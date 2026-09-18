"""An id the ledger itself put in the generated file is not a duplicate task.

A capture written into org/inbox.org is admitted to the ledger by the adapter,
and in a Phase 1 space the projector renders that item into the GENERATED
org/next_actions.org under the same :ID:. The namespace check refused the whole
space for it, and the cycle then projected nothing at all -- for every space on
the host. nightshift, 2026-09-18, 10:25Z until repaired by hand; 0-personal the
morning before.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_ingest_org as ingest  # noqa: E402
from ledger.log import EventLog  # noqa: E402

TASK = ("* TODO Decide the fee\n  :PROPERTIES:\n  :ID: {id}\n  :END:\n  body\n")


def _space(tmp_path: Path, *, phase1: bool = True) -> Path:
    space = tmp_path / "9-fixture"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org").mkdir(parents=True)
    if phase1:
        (space / ".datacore" / "ledger-phase").write_text("1\n")
    return space


def _admit(space: Path, identity: str) -> None:
    EventLog(space, "fixture").append("item.create", {
        "id": identity, "title": "Decide the fee", "state": "TODO", "space": space.name})


def test_the_generated_file_may_reproduce_an_id_the_inbox_still_holds(tmp_path):
    space = _space(tmp_path)
    identity = "b5596200-3ec9-4e8a-9ea7-04c9864d7b62"
    _admit(space, identity)
    (space / "org" / "inbox.org").write_text("* Inbox\n" + TASK.format(id=identity))
    (space / "org" / "next_actions.org").write_text("* Tasks\n" + TASK.format(id=identity))

    assert ingest._projected_duplicates(space, [space / "org" / "inbox.org",
                                                space / "org" / "next_actions.org"]) == {identity}


def test_an_id_in_two_files_that_the_ledger_never_saw_is_still_a_collision(tmp_path):
    space = _space(tmp_path)
    identity = "not-in-the-ledger-0001"
    (space / "org" / "inbox.org").write_text("* Inbox\n" + TASK.format(id=identity))
    (space / "org" / "next_actions.org").write_text("* Tasks\n" + TASK.format(id=identity))

    assert ingest._projected_duplicates(space, [space / "org" / "inbox.org",
                                                space / "org" / "next_actions.org"]) == set()


def test_a_phase_0_space_keeps_the_old_refusal(tmp_path):
    """Nothing is generated there, so one id in two files means two tasks."""
    space = _space(tmp_path, phase1=False)
    identity = "b5596200-3ec9-4e8a-9ea7-04c9864d7b62"
    _admit(space, identity)
    (space / "org" / "inbox.org").write_text("* Inbox\n" + TASK.format(id=identity))
    (space / "org" / "next_actions.org").write_text("* Tasks\n" + TASK.format(id=identity))

    assert ingest._projected_duplicates(space, [space / "org" / "inbox.org",
                                                space / "org" / "next_actions.org"]) == set()


def test_ensure_ids_completes_where_it_used_to_refuse_the_space(tmp_path, monkeypatch):
    """The end the outage was at: ensure_ids raised, ingest returned non-zero,
    and the cycle exited before projecting anything."""
    space = _space(tmp_path)
    identity = "b5596200-3ec9-4e8a-9ea7-04c9864d7b62"
    _admit(space, identity)
    (space / "org" / "inbox.org").write_text("* Inbox\n" + TASK.format(id=identity))
    (space / "org" / "next_actions.org").write_text("* Tasks\n" + TASK.format(id=identity))

    out = ingest.ensure_ids(space)

    assert "inbox.org:ok" in out and "next_actions.org:ok" in out and "1 projected" in out


def test_a_real_duplicate_inside_one_file_still_refuses(tmp_path):
    space = _space(tmp_path)
    identity = "b5596200-3ec9-4e8a-9ea7-04c9864d7b62"
    _admit(space, identity)
    (space / "org" / "inbox.org").write_text("* Inbox\n" + TASK.format(id=identity) + TASK.format(id=identity))
    (space / "org" / "next_actions.org").write_text("* Tasks\n" + TASK.format(id=identity))

    with pytest.raises(ValueError, match="duplicate Org IDs"):
        ingest.ensure_ids(space)
