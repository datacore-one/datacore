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
import json
import pytest
import sys

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.fold import ItemState, LedgerState                       # noqa: E402
from ledger_checkpoint import _fingerprint, compare, round_trip      # noqa: E402

@pytest.fixture(autouse=True)
def declared_backup_writer(monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "checkpoint-test")


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


@pytest.mark.parametrize('field,value', [('body', 'valuable notes'), ('properties', {'CUSTOM': 'valuable'}),
                                      ('priority', 'A'), ('created', '[2026-09-11 Fri 08:45]')])
def test_view_diagnostic_detects_nonheading_data_loss(field, value):
    from copy import deepcopy
    item = _item('task', 'Preserve all fields', level=1)
    item.payload['org'] = {field: value}
    original = _state(item)
    damaged = deepcopy(original)
    damaged.items['task'].payload['org'].pop(field)
    assert not compare(_fingerprint(original), _fingerprint(damaged))[0]
    live, restored, _ = round_trip(original, '9-view')
    assert compare(live, restored)[0]


def test_an_agents_completion_round_trips_as_review(tmp_path):
    """completed is live and renders REVIEW (2026-09-06); the restore must
    count it and read it back as the same item, not as one it invented."""
    import importlib.util, pathlib as _pl, sys as _sys
    LIB = _pl.Path(__file__).resolve().parents[1]
    _sys.path.insert(0, str(LIB))
    from ledger.log import EventLog
    spec = importlib.util.spec_from_file_location("ck", LIB / "ledger_checkpoint.py")
    ck = importlib.util.module_from_spec(spec); spec.loader.exec_module(ck)
    space = tmp_path / "5-plur"; (space / ".datacore" / "events").mkdir(parents=True); (space / "org").mkdir()
    log = EventLog(space, "nightshift")
    log.append("item.create", {"id": "t1", "title": "Publish the trust page", "state": "TODO", "tags": ["plur"]})
    log.append("item.claim", {"id": "t1", "executor": "server:nightshift"})
    log.append("item.complete", {"id": "t1"})
    ck.write(space)
    ok, detail = ck.verify(space)
    assert ok, detail


def _checkpoint_space(tmp_path):
    from ledger.log import EventLog
    space = tmp_path / '9-backup'
    log = EventLog(space, 'writer')
    log.append('item.create', {'id': 'a', 'title': 'preserve me', 'state': 'TODO', 'level': 1,
        'org': {'body': 'full body\n  indentation', 'priority': 'A', 'properties': {'CUSTOM': 'valuable'}},
        'genesis': {'date': '2024-02-03', 'rung': 'created_property'}})
    log.append('item.create', {'id': 'closed', 'title': 'completed history', 'private_metadata': {'keep': [1, 2]}})
    log.append('item.dismiss', {'id': 'closed', 'kind': 'done'})
    return space, log


def test_checkpoint_restores_saved_full_history_without_live_logs(tmp_path):
    import shutil
    import ledger_checkpoint as checkpoint
    from ledger.fold import fold
    from ledger.log import read_events
    space, _ = _checkpoint_space(tmp_path)
    expected = fold(read_events(space)).state_root()
    checkpoint.write(space)
    saved = json.loads((checkpoint.checkpoint_paths(space)[1]).read_text())
    shutil.rmtree(space / '.datacore/events')
    restored = checkpoint._restore(saved, space.name)
    assert restored.state_root() == expected
    assert restored.items['closed'].payload['private_metadata'] == {'keep': [1, 2]}
    assert restored.items['a'].payload['org']['body'] == 'full body\n  indentation'
    assert restored.items['a'].payload['org']['properties'] == {'CUSTOM': 'valuable'}


def test_verification_reads_saved_checkpoint_and_detects_corruption(tmp_path):
    import ledger_checkpoint as checkpoint
    space, log = _checkpoint_space(tmp_path)
    path = checkpoint.write(space)
    assert checkpoint.verify(space)[0]
    log.append('item.update', {'id': 'a', 'title': 'new work after backup'})
    ok, detail = checkpoint.verify(space)
    assert ok and 'older restore point' in detail
    path.write_text(path.read_text().replace('full body', 'corrupted body'))
    assert not checkpoint.verify(space)[0]
    assert 'corrupted body' in path.read_text(), 'verify must not rewrite the artifact it tests'


def test_tampered_chain_is_not_treated_as_a_stale_checkpoint(tmp_path):
    import ledger_checkpoint as checkpoint
    space, _ = _checkpoint_space(tmp_path)
    checkpoint.write(space)
    path = checkpoint.checkpoint_paths(space)[1]
    data = json.loads(path.read_text())
    key = next(iter(data['chains']))
    data['chains'][key] = data['chains'][key].replace('valuable', 'corrupt')
    path.write_text(json.dumps(data))
    assert not checkpoint.verify(space)[0]


def test_failed_checkpoint_pair_write_preserves_previous_backup(tmp_path, monkeypatch):
    import ledger_checkpoint as checkpoint
    import org_transaction as tx
    space, log = _checkpoint_space(tmp_path)
    checkpoint.write(space)
    paths = list(checkpoint.checkpoint_paths(space))
    before = [path.read_bytes() for path in paths]
    log.append('item.update', {'id': 'a', 'title': 'newer work'})
    original = tx.atomic_write_text
    def fail(path, content):
        if path == paths[1] and content.encode() != before[1]:
            raise OSError('simulated snapshot disk failure')
        return original(path, content)
    monkeypatch.setattr(tx, 'atomic_write_text', fail)
    with pytest.raises(OSError):
        checkpoint.write(space)
    assert [path.read_bytes() for path in paths] == before
    assert checkpoint.verify(space)[0]


def test_snapshot_restore_rejects_path_traversal(tmp_path):
    import ledger_checkpoint as checkpoint
    space, _ = _checkpoint_space(tmp_path)
    checkpoint.write(space)
    doc = json.loads((checkpoint.checkpoint_paths(space)[1]).read_text())
    doc['chains']['../../outside.jsonl'] = next(iter(doc['chains'].values()))
    with pytest.raises(ValueError, match='invalid saved chain'):
        checkpoint._restore(doc, space.name)


def test_lost_live_history_cannot_replace_last_good_backup(tmp_path):
    import ledger_checkpoint as checkpoint
    space, log = _checkpoint_space(tmp_path)
    checkpoint.write(space)
    paths = list(checkpoint.checkpoint_paths(space))
    saved = [path.read_bytes() for path in paths]
    log.path.unlink()
    with pytest.raises(ValueError, match='lost or replaced'):
        checkpoint.write(space)
    assert [path.read_bytes() for path in paths] == saved
    assert checkpoint.verify(space)[0]


def test_first_snapshot_upgrade_preserves_legacy_org_restore_point(tmp_path):
    import ledger_checkpoint as checkpoint
    space, _ = _checkpoint_space(tmp_path)
    old = space / checkpoint.CHECKPOINT_REL
    old.parent.mkdir(parents=True)
    old.write_text('valuable legacy-only backup\n')
    checkpoint.write(space)
    archived = list(old.parent.glob('*/legacy-*.org'))
    assert len(archived) == 1
    assert archived[0].read_text() == 'valuable legacy-only backup\n'
    assert checkpoint.verify(space)[0]


def test_two_backup_writers_use_disjoint_paths(tmp_path, monkeypatch):
    import ledger_checkpoint as checkpoint
    space, log = _checkpoint_space(tmp_path)
    monkeypatch.setenv('DATACORE_ACTOR', 'host-a')
    first = checkpoint.write(space)
    saved = first.read_bytes()
    log.append('item.update', {'id': 'a', 'title': 'new data'})
    monkeypatch.setenv('DATACORE_ACTOR', 'host-b')
    second = checkpoint.write(space)
    assert first != second and first.read_bytes() == saved
    assert checkpoint.verify(space)[0]
    monkeypatch.setenv('DATACORE_ACTOR', 'host-a')
    ok, detail = checkpoint.verify(space)
    assert ok and 'older restore point' in detail
