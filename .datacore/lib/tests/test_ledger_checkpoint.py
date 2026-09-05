"""The checkpoint round-trip must compare what a restore can actually carry.

Every false alarm this tool has raised was an asymmetry between the two sides
of the comparison, not a broken restore: filetags (2026-08-31), missing
states (2026-08-30), and — the one that kept winston's v2-verify red from
2026-08-31 to 2026-09-05 — a per-item `effective_tags` snapshot that nothing
refreshes when a parent's tags change or an item is re-filed, plus a tag
alphabet the renderer accepted and the parser did not. These tests pin the
comparison to a synthetic state so the invariant is checked without a real
space's contents being the fixture.
"""
import pathlib
import sys

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.fold import ItemState, LedgerState                       # noqa: E402
from ledger_checkpoint import _fingerprint, compare, round_trip      # noqa: E402


def _item(iid, title, *, level, tags=(), effective=None, parent=None,
          section=False, state="TODO", status="created"):
    payload = {"level": level, "tags": sorted(tags),
               "effective_tags": sorted(effective if effective is not None else tags),
               "parent": parent}
    if section:
        payload.update({"section": True, "state": None})
    else:
        payload["state"] = state
    return ItemState(id=iid, title=title, owner=None, status=status, payload=payload)


def _state(*items):
    return LedgerState(items={i.id: i for i in items})


def test_stale_effective_tags_snapshot_does_not_fail_a_correct_restore():
    """A child filed under `* Routed from inbox :inbox:routed:` after it was
    captured keeps the snapshot it was captured with. The projection nests it
    under that heading — as it must — and the re-import inherits the two tags.
    That is the ledger's CURRENT structure, faithfully restored."""
    st = _state(
        _item("sec", "Routed from inbox", level=1, tags=["inbox", "routed"], section=True),
        _item("task-stale", "Revise the digest", level=2, tags=["datacore"],
              effective=["datacore"], parent="sec"),
    )
    live, restored, _ = round_trip(st, "0-testspace")
    assert live["task-stale"][2] == ("datacore", "inbox", "routed"), live["task-stale"]
    assert compare(live, restored) == (True, "1 item(s) restore identically")


def test_parent_that_gained_a_tag_is_inherited_on_both_sides():
    """5-plur: five children whose parent had gained `AI` after they were
    recorded reported as 'altered' by a restore that preserved them."""
    st = _state(
        _item("epic", "Publish the article", level=1, tags=["AI", "comms", "plur"]),
        _item("child", "Stage A", level=2, tags=["engineering", "plur"],
              effective=["comms", "engineering", "plur"], parent="epic"),
    )
    live, restored, _ = round_trip(st, "5-testspace")
    assert live["child"][2] == ("AI", "comms", "engineering", "plur")
    assert compare(live, restored)[0], compare(live, restored)


def test_tag_block_with_a_hash_round_trips():
    """org-7aba0a999bc5: `:AI:pm:enterprise#373:` left inside a title by the
    original parser. The renderer split it out but kept `#`, which the parser
    cannot read, so the block came back half in the title and half as tags."""
    st = _state(
        _item("task-hash", "UX pass on 0.1.6 :AI:pm:enterprise#373:", level=1,
              tags=[], effective=["infra", "plur"], parent="gone-parent"),
    )
    live, restored, fresh = round_trip(st, "5-testspace")
    title, _state_, tags, *_ = live["task-hash"]
    assert title == "UX pass on 0.1.6", title
    assert tags == ("AI", "enterprise_373", "infra", "plur", "pm"), tags
    assert "enterprise#373" not in fresh, "the projection must be in the parser's alphabet"
    assert compare(live, restored)[0], compare(live, restored)


def test_promoted_orphan_carries_its_snapshot():
    """A parent absent from the projection cannot lend its tags, so the
    snapshot is rendered as the item's own — on both sides of the comparison."""
    st = _state(
        _item("orphan", "Floating task", level=2, tags=["x"],
              effective=["x", "inherited"], parent="closed-long-ago"),
    )
    live, restored, fresh = round_trip(st, "0-testspace")
    assert live["orphan"][2] == ("inherited", "x")
    assert "* TODO Floating task  :inherited:x:" in fresh
    assert compare(live, restored)[0], compare(live, restored)


def test_child_of_a_promoted_parent_inherits_the_parents_snapshot():
    """3-fds org-2cd3c1c78434: level 4 under a level-3 task whose own parent
    closed. The parent is promoted and renders its snapshot; the child, nested
    under it, inherits that snapshot -- on both sides."""
    st = _state(
        _item("parent", "Strip the token", level=3, tags=["infra", "security"],
              effective=["AI", "ceo", "infra", "security"], parent="closed-epic"),
        _item("child", "Decide participation", level=4, tags=["fds", "giveth"],
              effective=["fds", "giveth", "infra", "security"], parent="parent"),
    )
    live, restored, fresh = round_trip(st, "3-testspace")
    assert live["child"][2] == ("AI", "ceo", "fds", "giveth", "infra", "security")
    assert "* TODO Strip the token  :AI:ceo:infra:security:" in fresh
    assert compare(live, restored)[0], compare(live, restored)


def test_a_real_loss_is_still_reported():
    st = _state(_item("a", "Kept", level=1), _item("b", "Lost", level=1))
    live = _fingerprint(st)
    restored = {k: v for k, v in live.items() if k != "b"}
    ok, detail = compare(live, restored)
    assert not ok and detail == "1 lost (e.g. b)", detail
    ok, detail = compare(live, {**live, "a": ("Renamed", "TODO", (), None, None)})
    assert not ok and detail == "1 altered (e.g. a)", detail
