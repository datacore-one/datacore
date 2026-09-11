"""Projection replay must not depend on the host clock or timezone."""
import os
import time
import pytest

from ledger.fold import ItemState, LedgerState
from ledger.projector import project


def closed_state():
    item = ItemState('done', 'Preserved result', None, 'verified',
                     payload={'level': 1, 'tags': [], 'org': {'body': 'Evidence'}})
    item.closed_at = '1700000000000.0000.writer'
    return LedgerState(items={item.id: item})


def test_default_replay_does_not_expire_data_when_clock_advances(monkeypatch):
    state = closed_state()
    monkeypatch.setattr(time, 'time', lambda: 1700000001)
    before = project(state).text
    monkeypatch.setattr(time, 'time', lambda: 1701000000)
    assert project(state).text == before
    assert 'Preserved result' in before


def test_completion_timestamp_is_identical_across_host_timezones(monkeypatch):
    state = closed_state()
    monkeypatch.setattr(time, 'time', lambda: 1700000001)
    previous = os.environ.get('TZ')
    try:
        os.environ['TZ'] = 'UTC0'
        time.tzset()
        utc = project(state).text
        os.environ['TZ'] = 'EST5EDT'
        time.tzset()
        assert project(state).text == utc
        assert 'CLOSED: [2023-11-14 Tue 22:13]' in utc
    finally:
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()


def test_retention_uses_one_explicit_instant_and_never_reads_clock(monkeypatch):
    def forbidden():
        raise AssertionError('hidden clock input')
    monkeypatch.setattr(time, 'time', forbidden)
    state = closed_state()
    assert project(state, as_of=1700000000 + 86400).item_count == 1
    assert project(state, as_of=1700000000 + 86400.01).item_count == 0
    assert project(state).item_count == 1


@pytest.mark.parametrize('instant', [True, float('nan'), float('inf'), 'now'])
def test_invalid_retention_instant_is_refused(instant):
    with pytest.raises(ValueError, match='finite epoch seconds'):
        project(closed_state(), as_of=instant)


def test_foreign_space_file_tags_do_not_cross_projection_boundary():
    foreign = ItemState('foreign', 'Foreign', None, 'created',
                        payload={'space': '8-other', 'filetags': ['private_tag']})
    local = ItemState('local', 'Local', None, 'created', payload={'space': '9-fixture'})
    text = project(LedgerState(items={'foreign': foreign, 'local': local}), space='9-fixture').text
    assert 'Foreign' not in text
    assert 'private_tag' not in text


def test_cyclic_parent_links_are_refused_instead_of_silently_dropping_tasks():
    from ledger.edits import EditConflict
    first = ItemState('a', 'First', None, 'created', payload={'parent': 'b'})
    second = ItemState('b', 'Second', None, 'created', payload={'parent': 'a'})
    with pytest.raises(EditConflict, match='cyclic'):
        project(LedgerState(items={'a': first, 'b': second}))


def test_deep_valid_hierarchy_does_not_exhaust_python_recursion():
    items = {}
    for index in range(1200):
        identity = f'item-{index}'
        items[identity] = ItemState(identity, f'Task {index}', None, 'created', payload={
            'parent': f'item-{index - 1}' if index else None,
        })
    rendered = project(LedgerState(items=items))
    assert rendered.item_count == 1200
    assert ':ID: item-1199' in rendered.text


def test_filetag_selection_is_stable_under_equivalent_state_ordering():
    first = ItemState('a', 'First', None, 'created', payload={'filetags': ['first']})
    second = ItemState('b', 'Second', None, 'created', payload={'filetags': ['second']})
    assert project(LedgerState(items={'a': first, 'b': second})).text == project(
        LedgerState(items={'b': second, 'a': first})).text


def test_mixed_source_file_tags_keep_their_original_task_scope():
    from org_workspace._vendor.orgparse import loads
    first = ItemState('a', 'First', None, 'created', payload={'filetags': ['first']})
    second = ItemState('b', 'Second', None, 'created', payload={'filetags': ['second']})
    nodes = list(loads(project(LedgerState(items={'a': first, 'b': second})).text))[1:]
    assert {node.get_property('ID'): node.tags for node in nodes} == {
        'a': {'first'}, 'b': {'second'},
    }
