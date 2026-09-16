"""A properties drawer inside an item's body is never rendered, and never
ingested: the one drawer an item has comes from its payload.

5-plur, 2026-09-05..07: sixteen items carried a second drawer in their body
(an earlier id rewrite left the old drawer behind; ingest copied the body
verbatim), so the projection printed `:ID: org-70b200f505da` twice and the
id_churn detector failed hourly for two days."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger.fold import ItemState  # noqa: E402
from ledger.projector import render_item, strip_drawers  # noqa: E402

STRAY = "  :PROPERTIES:\n  :ID: org-70b200f505da\n  :END:\n"


def test_strip_drawers_removes_every_drawer_and_keeps_prose():
    assert strip_drawers("") == ""
    assert strip_drawers("plain prose") == "plain prose"
    assert strip_drawers(STRAY) == ""
    assert strip_drawers("before\n" + STRAY + "after") == "before\nafter"
    two = STRAY + "middle\n:PROPERTIES:\n:CREATED: [2026-07-15 Wed]\n:ID: org-old\n:END:\nend"
    assert strip_drawers(two) == "middle\nend"


def test_render_item_never_prints_a_drawer_from_the_body():
    item = ItemState(id="org-1", title="Send the inventory", owner=None, status="created",
                     payload={"state": "TODO", "org": {"body": STRAY + "notes here"}})
    text = "\n".join(render_item(item, level=2))
    assert text.count(":ID:") == 1
    assert ":ID: org-1" in text and "org-70b200f505da" not in text
    assert text.rstrip().endswith("notes here")


def test_genesis_strips_the_body_at_ingest():
    import ledger.genesis as g
    assert g._strip_drawers is strip_drawers


def test_planning_keywords_share_one_line_so_they_do_not_fall_into_the_body():
    """Org recognises CLOSED/SCHEDULED/DEADLINE only on ONE line under the heading.

    Rendered as two lines, everything after the first parses as body text. A
    task that was scheduled and then closed therefore grew a phantom
    "SCHEDULED: <...>" inside its body, which no authored file and no
    projection base could agree with — and since reconcile merges three ways,
    that one item took its whole space's ingest down with EditConflict on
    org.body, every cycle, until someone looked (2026-09-16, 0-personal).
    """
    import re
    item = ItemState(id="p1", title="Both", owner=None, status="dismissed",
                     closed_at="1789567920000",
                     payload={"scheduled": "2026-08-31", "deadline": "2026-09-02"})
    lines = render_item(item, level=1)
    planning = [l for l in lines if re.search(r'\b(CLOSED|SCHEDULED|DEADLINE):', l)]
    assert len(planning) == 1, f"planning keywords split across lines: {planning}"
    assert 'SCHEDULED:' in planning[0] and 'DEADLINE:' in planning[0], planning


def test_an_empty_logbook_drawer_is_not_body_content():
    """Emacs writes `:LOGBOOK:` / `:END:` by itself on a TODO state change.

    Two lines nobody typed, present in the authored file and absent from the
    ledger, are an unresolvable disagreement for a three-way merge. A logbook
    with entries in it is data and must survive untouched.
    """
    from ledger.projector import strip_empty_logbook
    assert strip_empty_logbook('  :LOGBOOK:\n  :END:\nreal text') == 'real text'
    kept = '  :LOGBOOK:\n  CLOCK: [2026-09-16 Wed 10:00]--[2026-09-16 Wed 11:00]\n  :END:\ntext'
    assert strip_empty_logbook(kept) == kept
    assert strip_empty_logbook('no drawer here') == 'no drawer here'
