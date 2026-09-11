"""Conflict previews must never become truncated or normalized replacement data."""
from datetime import datetime, timezone
import pytest
from sync.adapters.base import ExternalTask, OrgTask, Priority, TaskState
from sync.conflict import ConflictDetector, ConflictQueue, ConflictResolver


def pair(org_body, external_body):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    return (
        OrgTask(id='local', title='same', body=org_body, state=TaskState.NEXT,
                priority=Priority.A, scheduled=now, properties={'CUSTOM': 'keep'}),
        ExternalTask(id='remote', title='same', state='open', url='https://example.test/1',
                     created_at=now, updated_at=now, body=external_body, raw={'keep': ['all']}),
    )


@pytest.mark.parametrize('strategy', ['org_wins', 'external_wins', 'merge'])
def test_full_description_survives_detection_queue_and_resolution(tmp_path, strategy):
    org, remote = pair('  local\n' + 'a' * 4000 + '\nEND LOCAL\n', 'Remote\n' + 'b' * 4000 + '\nEND REMOTE\n')
    conflict = ConflictDetector().detect(org, remote)
    description = next(f for f in conflict.fields if f.field_name == 'description')
    description.last_synced_value = 'previous full description'
    queue = ConflictQueue(str(tmp_path / 'queue.db'))
    queue.add(conflict)
    loaded = queue.get_unresolved()[0]
    resolution = ConflictResolver({'description': strategy}).resolve(loaded)
    if strategy == 'org_wins':
        assert resolution.external_changes['description'] == org.body
    elif strategy == 'external_wins':
        assert resolution.org_changes['description'] == remote.body
    else:
        merged = resolution.org_changes['description']
        assert org.body in merged and remote.body in merged
        assert resolution.external_changes['description'] == merged
    assert loaded.org_task == org
    assert loaded.external_task == remote
    assert next(f for f in loaded.fields if f.field_name == 'description').last_synced_value == description.last_synced_value


def test_case_and_whitespace_are_data_and_repeated_merge_is_stable():
    resolver = ConflictResolver()
    original = '  KEY=Secret\n  significant indentation\n'
    external = 'key=secret\nsignificant indentation'
    merged = resolver._merge_descriptions(original, external)
    assert original in merged and external in merged
    assert resolver._merge_descriptions(merged, external) == merged
    conflict = ConflictDetector().detect(*pair('a\n  b', 'a b'))
    assert any(f.field_name == 'description' for f in conflict.fields)


def test_explicit_no_priority_round_trips_without_creating_a_conflict(tmp_path):
    org, remote=pair('original','different')
    org.priority=Priority.NONE
    conflict=ConflictDetector().detect(org,remote)
    queue=ConflictQueue(str(tmp_path/'conflicts.db')); queue.add(conflict)
    assert queue.get_unresolved()[0].org_task.priority is Priority.NONE
