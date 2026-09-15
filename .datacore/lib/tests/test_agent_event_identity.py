"""Agent event identity is the content, so one ID can never name two things.

The old scheme let a caller supply the identity. `emit_task` built
`task-{task_id}-{status}`, and CoS task ids carry a date, so every re-run that
day reused the ID with different content. `append_events` scanned EVERY
historical log, so a single July collision made every later write raise
EventConflict: 93 conflicting ids, oldest 2026-07-06, and the agent stream
recorded nothing from 2026-09-11 until this change.

Content addressing removes the failure mode rather than widening the tolerance:
equal content is the same event, different content is a different event.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import json  # noqa: E402

import pytest  # noqa: E402

from agent_stream_store import EventConflict, append_events  # noqa: E402


def row(**over):
    base = {'ts': '2026-09-15T10:00:00+00:00', 'type': 'agent.task.started',
            'agent': 'data', 'summary': 'triage started', 'severity': 'info',
            'details': None}
    base.update(over)
    from agent_emit import _event_identity
    base['id'] = _event_identity(base)
    return base


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_same_task_rerun_is_a_distinct_event(tmp_path):
    log = tmp_path / 'events-2026-09-15.jsonl'
    append_events(log, [row(ts='2026-09-15T19:42:04+00:00')])
    append_events(log, [row(ts='2026-09-15T20:05:24+00:00')])
    append_events(log, [row(ts='2026-09-15T21:12:10+00:00')])
    assert len(read(log)) == 3, 'three runs are three events'


def test_identical_content_deduplicates(tmp_path):
    log = tmp_path / 'events-2026-09-15.jsonl'
    append_events(log, [row()])
    assert append_events(log, [row()]) == 0, 'a true retry is not a new event'
    assert len(read(log)) == 1


def test_caller_dedup_key_still_dedups_across_differing_time(tmp_path):
    log = tmp_path / 'events-2026-09-15.jsonl'
    append_events(log, [row(dedup_key='telegram-123', ts='2026-09-15T10:00:00+00:00')])
    append_events(log, [row(dedup_key='telegram-123', ts='2026-09-15T10:00:05+00:00')])
    assert len(read(log)) == 1, 'one telegram message is one event'


def test_a_historical_duplicate_does_not_block_new_writes(tmp_path):
    """The exact 2026-09-11 outage: a poisoned old file must stay inert."""
    old = tmp_path / 'events-2026-07-24.jsonl'
    a = row(ts='2026-07-24T19:42:04+00:00', summary='first')
    b = dict(a, summary='second')          # same id, different content
    old.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in (a, b)), encoding='utf-8')

    today = tmp_path / 'events-2026-09-15.jsonl'
    assert append_events(today, [row(summary='new work')]) == 1


def test_a_contradiction_inside_todays_log_is_still_refused(tmp_path):
    """Identity is not a licence to rewrite: tampering must still be caught."""
    log = tmp_path / 'events-2026-09-15.jsonl'
    original = row(summary='as recorded')
    log.write_text(json.dumps(original, ensure_ascii=False) + '\n', encoding='utf-8')
    forged = dict(original, summary='rewritten')   # keeps the id, changes content
    with pytest.raises(EventConflict):
        append_events(log, [forged])


def test_emit_task_records_every_run_not_just_the_first(tmp_path, monkeypatch):
    """A correlation key must never be used as a dedup key.

    `task-{task_id}-{status}` names a task and, because CoS task ids carry a
    date, a day. Passing it as the dedup key collapsed every re-run that day
    into one record -- silently, which is worse than the 409 it replaced.
    """
    import agent_emit as emitter
    stream = tmp_path / 'agent-stream'
    stream.mkdir()
    monkeypatch.setattr(emitter, 'EVENT_LOG_DIR', stream)
    monkeypatch.setattr(emitter, '_RELAY_URL', None)

    for _ in range(3):
        emitter.emit_task('data', 'cos-cadence-triage', 'started',
                          task_id='cos-cadence-triage-2026-09-15')

    written = [json.loads(line) for f in stream.glob('events-*.jsonl')
               for line in f.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(written) == 3, 'three runs must be three records'
    assert all(e['details']['task_id'] == 'cos-cadence-triage-2026-09-15' for e in written), \
        'the task id still correlates the runs'
    assert len({e['id'] for e in written}) == 3, 'each run has its own identity'
