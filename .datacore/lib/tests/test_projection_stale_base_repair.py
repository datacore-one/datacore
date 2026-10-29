"""A stale projection base is repaired item by item, never swapped for the file (2026-10-29).

WHY. The base (last-rendered.json) is the three-way merge ancestor. Projection
writes it together with the file, but ingest admits a capture to the ledger
without moving it, so the base can lack items the file and the ledger both hold.
When the ledger then renders such an item differently from the file, the merge
reads the file's copy as a local edit and refuses it as "concurrent edit" -- every
cycle, because projection is skipped and the base never catches up. Winston saw
578 consecutive failures on 0-personal (2026-10-02..26) and fixed it on its pod
(6fc982f8) by adopting the whole current file as the base whenever the item sets
differ. That also fires on every ordinary capture and deletion: a capture became
"known", was never ingested and the next projection erased it; a deleted heading
came back from the ledger without the refusal that asks for deletion evidence.

The repair here touches only items whose membership differs AND whose ledger copy
moved on: there the ledger wins. Captures stay local additions (ingest first);
deletions of items the ledger did not change keep their refusal.
"""
import pytest
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.projector import project
from ledger.projection_state import STATE, base_document, guard_projection, reconcile, ProjectionConflict

SPACE = '9-drill'


def _create(log, ident, title):
    log.append('item.create', {'id': ident, 'title': title, 'state': 'TODO', 'space': SPACE, 'level': 1,
                               'tags': [], 'org': {'body': '', 'properties': {}, 'priority': None}})


def _render(space):
    return project(fold(read_events(space)), space=SPACE).text


def _space(tmp_path):
    space = tmp_path / SPACE
    (space / 'org').mkdir(parents=True)
    (space / '.datacore').mkdir(exist_ok=True)
    (space / '.datacore/ledger-edit-protocol').write_text('1\n')
    (space / STATE).parent.mkdir(parents=True)
    return space, EventLog(space, 'writer')


def test_an_item_the_base_never_saw_follows_the_ledger_instead_of_refusing_forever(tmp_path):
    space, log = _space(tmp_path)
    _create(log, 'one', 'first task')
    (space / STATE).write_text(base_document(_render(space)))      # base: one
    _create(log, 'two', 'captured task')                           # ingested, base not moved
    current = _render(space)                                       # file: one, two
    log.append('item.update', {'id': 'two', 'title': 'captured task, renamed'})
    proposed = _render(space)
    guard_projection(space, current, proposed)                     # used to raise: concurrent edit at items.two
    assert reconcile(space, current, proposed)['items']['two']['title'] == 'captured task, renamed'


def test_an_item_the_file_lost_while_the_ledger_changed_it_comes_back_from_the_ledger(tmp_path):
    space, log = _space(tmp_path)
    _create(log, 'one', 'first task')
    _create(log, 'two', 'second task')
    full = _render(space)
    (space / STATE).write_text(base_document(full))                # base: one, two
    current = '\n'.join(full.split('\n')[:full.split('\n').index(next(
        l for l in full.split('\n') if 'second task' in l))]) + '\n'  # file moved past: two gone
    log.append('item.update', {'id': 'two', 'title': 'second task, updated'})
    proposed = _render(space)
    merged = reconcile(space, current, proposed)                   # used to raise: concurrent edit at items.two
    assert merged['items']['two']['title'] == 'second task, updated'


def test_a_new_capture_is_never_adopted_as_known_and_erased(tmp_path):
    space, log = _space(tmp_path)
    _create(log, 'one', 'first task')
    text = _render(space)
    (space / STATE).write_text(base_document(text))
    current = text + '* TODO a thought I just had\n:PROPERTIES:\n:ID: cap-1\n:END:\n'
    merged = reconcile(space, current, text)
    assert 'cap-1' in merged['items'], 'the capture was dropped from the merge and would be erased'
    with pytest.raises(ProjectionConflict, match='not represented'):
        guard_projection(space, current, text)                     # projection waits for ingest


def test_a_deleted_heading_the_ledger_did_not_change_is_not_resurrected_silently(tmp_path):
    space, log = _space(tmp_path)
    _create(log, 'one', 'first task')
    _create(log, 'two', 'second task')
    full = _render(space)
    (space / STATE).write_text(base_document(full))
    lines = full.split('\n')
    cut = lines.index(next(l for l in lines if 'second task' in l))
    current = '\n'.join(lines[:cut]) + '\n'
    merged = reconcile(space, current, full)
    assert 'two' not in merged['items'], 'a deletion was overridden by the ledger without any evidence step'
