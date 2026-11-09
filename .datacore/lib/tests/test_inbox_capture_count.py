"""`org_workspace_adapter.py count --top-level` -- the number cos_inbox.sh reads to
decide whether a space's inbox gets a model run at all ("0": tidied, no model run).

5-plur, 2026-10-03: the inbox held only an empty group heading ("* Continuation
from wrap-up 2026-09-30 (...)" with a :PROPERTIES: drawer and no children left).
Stateless headings were never counted, so the count read 0, the processor never
ran, and the container it is told to remove (gtd-inbox-processor.md, "Group
heading") stayed every night -- and INB-8 reads a stateless level-1 heading with
nothing under it as an unprocessed capture.
"""
from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import org_workspace_adapter as A  # noqa: E402


def _count(tmp_path, text: str) -> int:
    f = tmp_path / "inbox.org"
    f.write_text(text)
    return A.cmd_count(Namespace(files=[str(f)], top_level=True))["count"]


HEAD = "#+TITLE: Inbox\n* Inbox\n  :PROPERTIES:\n  :ID: sec\n  :END:\n"


def test_an_emptied_group_heading_is_something_to_process(tmp_path):
    text = HEAD + ("* Continuation from wrap-up 2026-09-30 (release hardening)\n"
                   "  :PROPERTIES:\n  :ID: grp\n  :END:\n")
    assert _count(tmp_path, text) == 1


def test_a_stateless_bare_capture_is_something_to_process(tmp_path):
    assert _count(tmp_path, HEAD + "* blue thing w/ Marko??\n") == 1


def test_a_group_still_holding_entries_is_a_container_not_a_capture(tmp_path):
    """Its open children are parked for the owner by design (INB-8 reads them so)."""
    text = HEAD + ("* Continuation from wrap-up 2026-10-01\n  :PROPERTIES:\n  :ID: grp\n  :END:\n"
                   "** TODO Back-merge main\n** DONE Pushed the tag\n")
    assert _count(tmp_path, text) == 0


def test_an_empty_inbox_section_and_finished_entries_are_nothing(tmp_path):
    assert _count(tmp_path, HEAD + "** DONE Renewed the insurance\n") == 0


def test_a_heading_already_marked_for_review_is_not_counted_again(tmp_path):
    assert _count(tmp_path, HEAD + "* [NEEDS_REVIEW] TIER 2: SUPPORTING WORK\nnotes\n") == 0
