"""Queued conflicts must never become truncated or normalized replacement data.

The detector/resolver halves of these tests went with those classes on
2026-09-23 (owner decision P7). What remains is the queue's round trip: a
conflict record, with its full task snapshots, comes back exactly as stored.
"""
from datetime import datetime, timezone
from sync.adapters.base import ExternalTask, OrgTask, Priority, TaskState
from sync.conflict import Conflict, ConflictField, ConflictQueue, ConflictType


def pair(org_body, external_body):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    return (
        OrgTask(id='local', title='same', body=org_body, state=TaskState.NEXT,
                priority=Priority.A, scheduled=now, properties={'CUSTOM': 'keep'}),
        ExternalTask(id='remote', title='same', state='open', url='https://example.test/1',
                     created_at=now, updated_at=now, body=external_body, raw={'keep': ['all']}),
    )


def conflict_of(org, remote):
    return Conflict(external_id='github:o/r#1', org_task_id=org.id, org_task=org,
                    external_task=remote,
                    fields=[ConflictField('description', ConflictType.DESCRIPTION,
                                          org.body, remote.body)])


def test_full_description_survives_the_queue(tmp_path):
    org, remote = pair('  local\n' + 'a' * 4000 + '\nEND LOCAL\n', 'Remote\n' + 'b' * 4000 + '\nEND REMOTE\n')
    conflict = conflict_of(org, remote)
    conflict.fields[0].last_synced_value = 'previous full description'
    queue = ConflictQueue(str(tmp_path / 'queue.db'))
    queue.add(conflict)
    loaded = queue.get_unresolved()[0]
    field = loaded.fields[0]
    assert (field.org_value, field.external_value) == (org.body, remote.body)
    assert loaded.org_task == org
    assert loaded.external_task == remote
    assert field.last_synced_value == 'previous full description'


def test_explicit_no_priority_round_trips(tmp_path):
    org, remote = pair('original', 'different')
    org.priority = Priority.NONE
    queue = ConflictQueue(str(tmp_path / 'conflicts.db'))
    queue.add(conflict_of(org, remote))
    assert queue.get_unresolved()[0].org_task.priority is Priority.NONE
