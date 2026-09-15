"""Two independent replicas must preserve disjoint edits and expose conflicts."""
from copy import deepcopy
import shutil

import pytest
from ledger.edits import conditional_payload, EditConflict
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.projector import project
from ledger.projection_state import sync_generated
from ledger.projection_state import STATE, base_document

def setup(tmp_path):
    space = tmp_path / "9-drill"
    (space / "org").mkdir(parents=True)
    (space / '.datacore').mkdir(exist_ok=True)
    (space / '.datacore/ledger-edit-protocol').write_text('1\n')
    log = EventLog(space, "writer")
    log.append("item.create", {"id": "one", "title": "original title", "state": "TODO", "space": space.name,
        "level": 1, "tags": [], "org": {"body": "original body", "properties": {"A": "base"}, "priority": None}})
    text = project(fold(read_events(space))).text
    (space / "org/next_actions.org").write_text(text)
    (space / STATE).parent.mkdir(parents=True)
    (space / STATE).write_text(base_document(text))
    return space, log, text


def replicas(tmp_path):
    left, _, text = setup(tmp_path / 'host-a')
    right = tmp_path / 'host-b' / left.name
    shutil.copytree(left, right)
    return left, right, text


def combined(left, right):
    events = {event.hash: event for event in read_events(left) + read_events(right)}
    return sorted(events.values(), key=lambda event: event.hlc)


def test_independently_prepared_disjoint_property_changes_survive_convergence(tmp_path):
    left, right, text = replicas(tmp_path)
    (left / 'org/next_actions.org').write_text(text.replace(':A: base', ':A: left'))
    (right / 'org/next_actions.org').write_text(text.replace(':A: base', ':A: base\n  :B: right'))
    sync_generated(left, fold(read_events(left)), 'host-a')
    sync_generated(right, fold(read_events(right)), 'host-b')
    events = combined(left, right)
    original = deepcopy(events)
    state = fold(events)
    assert state.items['one'].payload['org']['properties'] == {'A': 'left', 'B': 'right'}
    assert state.items['one'].edit_conflicts == {}
    assert events == original
    assert 'left' in project(state).text and 'right' in project(state).text


def test_divergent_replicated_edits_block_projection_and_retain_both_proposals(tmp_path):
    left, right, text = replicas(tmp_path)
    for path, actor, title in ((left, 'host-a', 'left title'), (right, 'host-b', 'right title')):
        (path / 'org/next_actions.org').write_text(text.replace('original title', title))
        sync_generated(path, fold(read_events(path)), actor)
    events = combined(left, right)
    state = fold(events)
    assert len(state.items['one'].edit_conflicts) == 1
    assert {e.payload.get('title') for e in events if e.type == 'item.update'} == {'left title', 'right title'}
    with pytest.raises(EditConflict, match='unresolved'):
        project(state)
    assert 'left title' in (left / 'org/next_actions.org').read_text()
    assert 'right title' in (right / 'org/next_actions.org').read_text()
    # An explicit reconciliation names the conflict and supplies a fresh base.
    current = state.items['one']
    payload = conditional_payload(current, {'title': 'left title / right title'}, resolves=current.edit_conflicts)
    resolution = EventLog(right, 'host-b').append('item.update', payload)
    final = fold(events + [resolution])
    assert not final.items['one'].edit_conflicts
    assert 'left title / right title' in project(final).text


def test_stale_terminal_transition_cannot_hide_new_remote_work(tmp_path):
    left, right, text = replicas(tmp_path)
    (left / 'org/next_actions.org').write_text(text.replace('original title', 'new remote work'))
    sync_generated(left, fold(read_events(left)), 'host-a')
    (right / 'org/next_actions.org').write_text(text.replace('* TODO ', '* DONE '))
    sync_generated(right, fold(read_events(right)), 'host-b')
    state = fold(combined(left, right))
    assert state.items['one'].status != 'dismissed'
    assert state.items['one'].title == 'new remote work'
    assert state.items['one'].edit_conflicts
    with pytest.raises(EditConflict):
        project(state)


def test_remove_property_and_change_other_property_merge(tmp_path):
    left, right, text = replicas(tmp_path)
    # Removal is explicit in a conditional full-map edit, never an inference
    # made from an unconditioned partial update.
    before = fold(read_events(left)).items['one']
    EventLog(left, 'host-a').append('item.update', conditional_payload(before, {'org': {'body': 'original body', 'priority': None, 'properties': {}}}))
    EventLog(right, 'host-b').append('item.update', conditional_payload(before, {'org': {'body': 'original body', 'priority': None, 'properties': {'A': 'base', 'B': 'right'}}}))
    state = fold(combined(left, right))
    assert state.items['one'].payload['org']['properties'] == {'B': 'right'}
    assert not state.items['one'].edit_conflicts


@pytest.mark.parametrize('condition', [None, [], {}, {'version': 1, 'base': []}, {'version': 99}])
def test_malformed_condition_fails_without_mutating_or_crashing(tmp_path, condition):
    space, log, _ = setup(tmp_path)
    log.append('item.update', {'id': 'one', 'title': 'must not win', '_merge': condition})
    state = fold(read_events(space))
    assert state.items['one'].title == 'original title'
    assert state.items['one'].edit_conflicts


def test_unresolved_edit_conflicts_prevent_claim_and_executor_start(tmp_path, monkeypatch):
    from ledger.policy import approval_payload_hash, PolicyError
    from executors.base import Executor
    import tool_policy
    space, log, _ = setup(tmp_path)
    initial = fold(read_events(space)).items['one']
    log.append('item.claim', {'id': 'one', 'payload_hash': approval_payload_hash(initial.payload)})
    # This edit was prepared before the claim, so it must not change a task
    # already selected for execution. It remains a visible conflict instead.
    log.append('item.update', conditional_payload(initial, {'title': 'different work'}))
    state = fold(read_events(space))
    assert state.items['one'].edit_conflicts
    assert state.items['one'].title == 'original title'
    executor = Executor()
    executor._actor, executor._item, executor._space = 'writer', 'one', space
    monkeypatch.setattr(tool_policy, 'principal_for', lambda actor: actor)
    with pytest.raises(PolicyError, match='replicated edits'):
        executor._execution_env()
    log.append('item.complete', {'id': 'one'})
    assert fold(read_events(space)).items['one'].status == 'claimed'


def test_conditional_events_cannot_start_before_explicit_fleet_upgrade(tmp_path):
    from ledger.edits import PROTOCOL
    space, log, _ = setup(tmp_path)
    before = read_events(space)
    payload = conditional_payload(fold(before).items['one'], {'title': 'new protocol data'})
    (space / PROTOCOL).unlink()
    with pytest.raises(EditConflict, match='all active readers'):
        log.append('item.update', payload)
    assert read_events(space) == before
    (space / PROTOCOL).write_text('unsupported-version\n')
    with pytest.raises(EditConflict):
        log.append('item.update', payload)
    assert read_events(space) == before
