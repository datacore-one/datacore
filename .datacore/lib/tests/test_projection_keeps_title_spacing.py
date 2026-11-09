"""A title renders as the ledger holds it; only line breaks become spaces.

`_clean_title_and_tags` joined `title.split()`, which collapses EVERY run of
whitespace, not only the newlines it was written for. A title with a literal
`":   "` in it (2-datacore's org-20260925-continue-template-multiline, about
org's multi-line property prefix) was stored with three spaces, rendered with
one, and read back with one -- so the projection disagreed with the ledger
about an item nobody had touched. In a phase-1 space the three-way merge calls
that a concurrent edit, and from 2026-09-30 07:07Z winston's 2-datacore ingest
and phase-1 cycle failed every hour on it.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import _clean_title_and_tags, project  # noqa: E402
from ledger.projection_state import snapshot  # noqa: E402

TITLE = 'Fix the /continue template: multi-line properties need the ":   " prefix'


def test_inner_spacing_survives_the_render():
    assert _clean_title_and_tags(TITLE, [])[0] == TITLE


def test_line_breaks_still_become_one_line():
    assert _clean_title_and_tags("first line\nNo other content\r\n  third", [])[0] == \
        "first line No other content third"


def test_surrounding_space_is_still_trimmed():
    assert _clean_title_and_tags("  padded  ", [])[0] == "padded"


def test_the_projection_reads_back_the_title_the_ledger_holds(tmp_path):
    space = tmp_path / "9-fixture"
    (space / "org").mkdir(parents=True)
    EventLog(space, "writer").append("item.create", {
        "id": "one", "title": TITLE, "state": "TODO", "space": space.name, "level": 1,
        "tags": [], "org": {"body": "", "properties": {}, "priority": None}})

    text = project(fold(read_events(space)), space=space.name).text

    assert snapshot(text, space.name)["items"]["one"]["title"] == TITLE
