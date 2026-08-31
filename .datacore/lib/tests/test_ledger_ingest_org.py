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


TAG_ORG = """\
#+TITLE: Next Actions

* NEXT Gains a tag in org                                          :work:urgent:
:PROPERTIES:
:ID: task-gains
:END:
* NEXT Ledger knows more than the heading                                :work:
:PROPERTIES:
:ID: task-superset
:END:
"""


@pytest.fixture
def tag_space(tmp_path):
    space = tmp_path / "0-tagspace"
    (space / "org").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(TAG_ORG, encoding="utf-8")

    log = EventLog(space, "test")
    log.append("item.create", {"id": "task-gains", "title": "Gains a tag in org",
                               "state": "NEXT", "tags": ["work"]})
    # Ancestor tags baked in by a genesis run predating `shallow_tags`.
    log.append("item.create", {"id": "task-superset",
                               "title": "Ledger knows more than the heading",
                               "state": "NEXT", "tags": ["gtd", "inherited", "work"]})
    return space


def _tags(space, nid):
    return (fold(read_events(space)).items[nid].payload or {}).get("tags")


def test_tag_added_in_org_reaches_the_ledger(tag_space):
    """The hole fill-when-empty left open.

    Without this the tag vanishes at the Phase 1 flip, when the projection
    replaces the authored file.
    """
    ingest.sync_state(tag_space, actor="test")
    assert _tags(tag_space, "task-gains") == ["urgent", "work"]


def test_ledger_only_tags_are_never_removed(tag_space):
    """39 real items depend on this.

    Their payloads carry ancestor tags from a genesis run that had no
    `shallow_tags`. Those tags are what the projector renders today, so
    "correcting" them to match the heading would delete live data to satisfy
    a comparison.
    """
    ingest.sync_state(tag_space, actor="test")
    assert _tags(tag_space, "task-superset") == ["gtd", "inherited", "work"]


def test_effective_tags_claim_inherited_only_with_recorded_structure(tmp_path):
    """effective_tags must equal what the projection will actually render.

    The projector reproduces a section for an item that has a `parent`; for
    one that does not, it renders the heading at top level and the inherited
    tags simply are not in the file. Recording them anyway makes the
    checkpoint round-trip compare a claim against a file that cannot contain
    it, and reports a restore that worked as one that corrupted the item.
    """
    space = tmp_path / "0-structspace"
    (space / "org").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(
        "* Section                                                     :routed:\n"
        "** NEXT Structured\n:PROPERTIES:\n:ID: task-structured\n:END:\n"
        "** NEXT Floating\n:PROPERTIES:\n:ID: task-floating\n:END:\n",
        encoding="utf-8")

    log = EventLog(space, "test")
    log.append("item.create", {"id": "task-structured", "title": "Structured",
                               "state": "NEXT", "parent": "sec", "level": 2})
    log.append("item.create", {"id": "task-floating", "title": "Floating",
                               "state": "NEXT"})

    ingest.sync_state(space, actor="test")
    items = fold(read_events(space)).items

    assert "routed" in (items["task-structured"].payload or {}).get("effective_tags", [])
    assert "routed" not in (items["task-floating"].payload or {}).get("effective_tags", [])


def test_tag_sync_settles(tag_space):
    """No update once merged — an unstable sync would append events hourly."""
    ingest.sync_state(tag_space, actor="test")
    n = len(read_events(tag_space))

    assert ingest.sync_state(tag_space, actor="test")["updated"] == 0
    assert len(read_events(tag_space)) == n


def test_archived_heading_closes_as_housekeeping(space):
    """org-archive-subtree moves a heading into <file>_archive.org — a plain
    file move nothing here can hook, so the ledger only ever learns about it
    by finding the id in the archive."""
    (space / "org" / "next_actions_archive.org").write_text(
        "* NEXT Still working on it\n:PROPERTIES:\n:ID: task-next\n:END:\n",
        encoding="utf-8")
    # And it is gone from the live file.
    live = (space / "org" / "next_actions.org")
    live.write_text(live.read_text().split("* NEXT Still working on it")[0],
                    encoding="utf-8")

    ingest.sync_state(space, actor="test")

    item = _items(space)["task-next"]
    assert item.status == "dismissed"
    assert closure_kind(item) == "housekeeping", (
        "archived is neither finished nor abandoned"
    )


def test_absence_alone_never_dismisses(space):
    """The guard that keeps this from becoming a bulk-close.

    A truncated or half-written next_actions.org must not terminally close
    live work — dismiss is terminal (DIP-0034), so absence is far too weak a
    signal to act on.
    """
    (space / "org" / "next_actions.org").write_text("", encoding="utf-8")

    ingest.sync_state(space, actor="test")

    assert all(_items(space)[i].status in ingest.LIVE for i in IDS)


def test_archived_but_still_authored_elsewhere_stays_live(space):
    """Archived out of next_actions.org, still captured in inbox.org."""
    (space / "org" / "next_actions_archive.org").write_text(
        "* NEXT Still working on it\n:PROPERTIES:\n:ID: task-next\n:END:\n",
        encoding="utf-8")
    (space / "org" / "inbox.org").write_text(
        "* Still working on it\n:PROPERTIES:\n:ID: task-next\n:END:\n",
        encoding="utf-8")

    ingest.sync_state(space, actor="test")

    assert _items(space)["task-next"].status in ingest.LIVE


def test_terminal_kinds_match_the_ratified_loop():
    """Guards the mapping itself against a well-meaning edit.

    Adding DEFERRED here would silently break the wake path, and the failure
    would surface as tasks quietly never coming back rather than as an error.
    """
    assert ingest.TERMINAL_KINDS == {"DONE": "done", "CANCELLED": "dropped"}
