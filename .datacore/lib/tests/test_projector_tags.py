"""The projector must always write valid org: no tag text inside a title, no
hyphen in a tag. A hyphenated tag block left in a title by ingest failed the
org-tag hook on every winston autosave of 5-plur from 2026-09-03."""
import pathlib, sys

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.fold import ItemState          # noqa: E402
from ledger.projector import render_item, _clean_title_and_tags, _org_stamp  # noqa: E402


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



def test_typed_weekday_in_a_stamp_is_recomputed():
    """4-forge's checkpoint carried <2026-07-30 Wed>; 2026-07-30 was a Thursday."""
    assert _org_stamp("<2026-07-30 Wed>") == "<2026-07-30 Thu>"
    assert _org_stamp("[2026-07-30 Wed 09:00]") == "[2026-07-30 Thu 09:00]"
    assert _org_stamp("<2026-07-30 Thu +1w>") == "<2026-07-30 Thu +1w>", "a correct stamp is untouched"
    assert _org_stamp("2026-07-30").startswith("<2026-07-30 Thu"), "bare dates still get bracketed with the right day"


def test_typed_weekday_inside_body_text_is_recomputed():
    """4-forge: a DEADLINE line captured as body text carried <2026-07-30 Wed>."""
    item = _item("Generate SEO blog post")
    item.payload["org"] = {"body": "DEADLINE: <2026-07-30 Wed>\nGenerate the post."}
    text = "\n".join(render_item(item))
    assert "<2026-07-30 Thu>" in text and "Wed" not in text


def test_hash_and_percent_are_outside_the_parsers_alphabet():
    """Org admits `#` and `%` in a tag; the importer this projection must
    round-trip through does not. `:AI:enterprise#373:infra:pm:` came back from
    that parser as tags (infra, pm) and a title ending in ` :AI:enterprise#373`
    (5-plur org-7aba0a999bc5, 2026-08-31). The renderer's alphabet is the
    parser's, so what is written is what is read back."""
    line = render_item(_item("Plain title", tags=["enterprise#373", "a%b", "ok"]))[0]
    assert line.endswith("  :a_b:enterprise_373:ok:"), line
    from org_workspace._vendor.orgparse import loads
    node = loads(line + "\n").children[0]
    assert node.heading == "Plain title" and sorted(node.tags) == ["a_b", "enterprise_373", "ok"]


def test_org_mode_tag_alphabet_is_kept_verbatim():
    """`enterprise#373` is a legal org tag. Sanitising it to `enterprise_373`
    made the projection disagree with the authored file forever (5-plur,
    org-7aba0a999bc5, 2026-09-06)."""
    from ledger.projector import _clean_title_and_tags
    title, tags = _clean_title_and_tags("UX pass on 0.1.6 :AI:pm:enterprise#373:", None)
    assert title == "UX pass on 0.1.6"
    assert tags == ["AI", "enterprise#373", "pm"]
    _, tags = _clean_title_and_tags("x", ["a b", "100%", "c:d"])
    assert tags == ["100%", "a_b", "c_d"]
