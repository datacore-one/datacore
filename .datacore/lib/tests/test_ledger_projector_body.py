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
