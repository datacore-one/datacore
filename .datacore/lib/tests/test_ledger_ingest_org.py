"""Tests for ledger_ingest_org.sync_state -- org state -> ledger reconciliation.

The case these pin is the one that broke: which org states close an item in
the ledger. DIP-0009 v2.0 rules `DONE, CANCELLED -> terminal` and DEFERRED
"closed but wakeable, non-terminal", so the first two must dismiss and the
third must not. While CANCELLED was left out, 33 cancelled tasks across four
spaces stayed live in the projection forever, `all_clean` could never become
true, and box-projection-drift alerted nightly about something no run could
clear.

A real space dir on tmp_path with a real event log -- sync_state reads org
through org_workspace and folds actual events, and mocking either would test
the mock rather than the reconciliation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import ledger_ingest_org as ingest  # noqa: E402
from ledger.fold import closure_kind, fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402

ORG = """\
#+TITLE: Next Actions
#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) REVIEW(r!) | DONE(d!) DEFERRED(f!) CANCELLED(c!)

* DONE Ship the thing
:PROPERTIES:
:ID: task-done
:END:
* CANCELLED Do not ship the other thing
:PROPERTIES:
:ID: task-cancelled
:END:
* DEFERRED Ship it later
:PROPERTIES:
:ID: task-deferred
:END:
SCHEDULED: <2099-01-01 Fri>
* NEXT Still working on it
:PROPERTIES:
:ID: task-next
:END:
"""

IDS = ("task-done", "task-cancelled", "task-deferred", "task-next")


@pytest.fixture
def space(tmp_path):
    """A space whose four tasks all exist as live ledger items."""
    space = tmp_path / "0-testspace"
    (space / "org").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(ORG, encoding="utf-8")

    log = EventLog(space, "test")
    for nid in IDS:
        log.append("item.create", {"id": nid, "title": nid})
    return space


def _items(space):
    return fold(read_events(space)).items


def test_done_and_cancelled_dismiss_deferred_does_not(space):
    result = ingest.sync_state(space, actor="test")

    assert result["dismissed"] == 2, "exactly DONE + CANCELLED"

    items = _items(space)
    assert items["task-done"].status == "dismissed"
    assert items["task-cancelled"].status == "dismissed"
    assert items["task-deferred"].status in ingest.LIVE, (
        "DEFERRED is closed-but-wakeable: dismissing it (terminal per "
        "DIP-0034) would make the scheduled wake impossible"
    )
    assert items["task-next"].status in ingest.LIVE


def test_cancelled_closes_as_dropped_not_done(space):
    """Cancelled work must not count toward completion stats."""
    ingest.sync_state(space, actor="test")
    items = _items(space)

    assert closure_kind(items["task-done"]) == "done"
    assert closure_kind(items["task-cancelled"]) == "dropped"


def test_dry_run_reports_without_emitting(space):
    before = len(read_events(space))
    result = ingest.sync_state(space, actor="test", dry_run=True)

    assert result["dismissed"] == 2
    assert len(read_events(space)) == before
    assert all(_items(space)[i].status in ingest.LIVE for i in IDS)


def test_reconciliation_is_idempotent(space):
    """A second pass must not re-dismiss what it already closed.

    sync_state runs hourly; an ingest that appended a fresh dismiss on every
    run would grow the log without bound and make the projection's history
    useless for reading why an item closed.
    """
    ingest.sync_state(space, actor="test")
    after_first = len(read_events(space))

    result = ingest.sync_state(space, actor="test")

    assert result["dismissed"] == 0
    assert len(read_events(space)) == after_first


def test_terminal_kinds_match_the_ratified_loop():
    """Guards the mapping itself against a well-meaning edit.

    Adding DEFERRED here would silently break the wake path, and the failure
    would surface as tasks quietly never coming back rather than as an error.
    """
    assert ingest.TERMINAL_KINDS == {"DONE": "done", "CANCELLED": "dropped"}
