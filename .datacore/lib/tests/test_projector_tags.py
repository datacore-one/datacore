"""The projector must always write valid org: no tag text inside a title, no
hyphen in a tag. A hyphenated tag block left in a title by ingest failed the
org-tag hook on every winston autosave of 5-plur from 2026-09-03."""
import pathlib, sys

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.fold import ItemState          # noqa: E402
from ledger.projector import render_item, _clean_title_and_tags  # noqa: E402


def _item(title, tags=None, status="created", state="TODO"):
    return ItemState(id="org-x", title=title, owner=None, status=status,
                     payload={"level": 1, "state": state, "tags": tags or []})


def test_embedded_tag_block_is_split_out_and_normalised():
    line = render_item(_item("Fix the Provenance Ladder's version stamps   :provenance:docs:wrap-up-extracted:"))[0]
    assert line == "* TODO Fix the Provenance Ladder's version stamps  :docs:provenance:wrap_up_extracted:", line


def test_invalid_characters_in_declared_tags_become_underscores():
    line = render_item(_item("Plain title", tags=["wrap-up-extracted", "ok_tag", "a.b"]))[0]
    assert line.endswith("  :a_b:ok_tag:wrap_up_extracted:"), line


def test_ordinary_titles_and_tags_are_untouched():
    assert render_item(_item("Ship it", tags=["AI", "plur"]))[0] == "* TODO Ship it  :AI:plur:"
    assert render_item(_item("No tags here"))[0] == "* TODO No tags here"
    assert _clean_title_and_tags("ratio 1:2 in title", []) == ("ratio 1:2 in title", [])
