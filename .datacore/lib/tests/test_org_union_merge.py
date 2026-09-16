"""Two diverged copies of one Org file must lose nothing when reconciled.

Measured 2026-09-16 on 0-personal/org/inbox.org: 241 items existed only on the
executor host, 140 only on origin, 4 on both, and exactly ONE of those four
differed. Git offers only whole-side resolutions there, each discarding
hundreds of real captures.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

from org_union_merge import reconcile, split  # noqa: E402

PRE = "#+TITLE: Inbox\n\n"


def item(iid, state="TODO", title="A task", extra=""):
    return f"** {state} {title}\n:PROPERTIES:\n:ID: {iid}\n:END:\n{extra}"


def ids(text):
    return [i for i, _ in split(text)[1]]


def test_nothing_is_lost_from_either_side():
    ours = PRE + item("a") + item("b")
    theirs = PRE + item("c") + item("d")
    merged, stats = reconcile(ours, theirs)
    assert ids(merged) == ["a", "b", "c", "d"]
    assert stats["kept_ours"] == 2 and stats["kept_theirs"] == 2


def test_an_item_on_both_sides_appears_once():
    ours = PRE + item("a") + item("b")
    theirs = PRE + item("b") + item("c")
    merged, _ = reconcile(ours, theirs)
    assert ids(merged) == ["a", "b", "c"]
    assert merged.count(":ID: b") == 1


def test_a_recorded_closure_beats_an_open_copy():
    """The real case: one side never saw the completion."""
    open_ = item("a", "TODO")
    done = item("a", "DONE", extra="CLOSED: [2026-09-10 Thu 14:39]\n")
    merged, stats = reconcile(PRE + open_, PRE + done)
    assert "DONE" in merged and "CLOSED:" in merged
    assert stats["closure_wins"] == 1
    # and in the other direction
    merged, _ = reconcile(PRE + done, PRE + open_)
    assert "DONE" in merged


def test_a_closure_without_a_stamp_does_not_win():
    """DONE with no CLOSED: is not evidence of when, so it decides nothing."""
    with pytest.raises(ValueError):
        reconcile(PRE + item("a", "TODO"), PRE + item("a", "DONE"))


def test_deferred_is_not_terminal():
    """DEFERRED is closed-but-wakeable; it must not silently beat a live TODO."""
    with pytest.raises(ValueError):
        reconcile(PRE + item("a", "TODO"),
                  PRE + item("a", "DEFERRED", extra="CLOSED: [2026-09-10 Thu 14:39]\n"))


def test_a_real_disagreement_is_refused_and_named():
    ours = PRE + item("a", "TODO", "One title")
    theirs = PRE + item("a", "TODO", "A different title")
    with pytest.raises(ValueError, match="a"):
        reconcile(ours, theirs)


def test_two_closures_that_disagree_are_refused():
    a = item("a", "DONE", extra="CLOSED: [2026-09-10 Thu 14:39]\n")
    b = item("a", "CANCELLED", extra="CLOSED: [2026-09-11 Fri 09:00]\n")
    with pytest.raises(ValueError):
        reconcile(PRE + a, PRE + b)


def test_the_preamble_is_preserved_verbatim():
    ours = "#+TITLE: Inbox\n#+FILETAGS: :gtd:\n\n" + item("a")
    merged, _ = reconcile(ours, PRE + item("b"))
    assert merged.startswith("#+TITLE: Inbox\n#+FILETAGS: :gtd:\n\n")


def test_headings_without_an_id_survive():
    ours = PRE + "* Inbox\n" + item("a")
    merged, stats = reconcile(ours, PRE + item("b"))
    assert "* Inbox\n" in merged and stats["unidentified"] >= 1


def test_identical_files_are_unchanged():
    ours = PRE + item("a") + item("b")
    merged, _ = reconcile(ours, ours)
    assert ids(merged) == ["a", "b"]
