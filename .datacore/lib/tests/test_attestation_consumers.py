"""Verification summaries must use canonical events and actual writer identity."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import actor_identity
import claim_gate
import reliability_scoreboard
from ledger.events import body_dict, compute_hash
from ledger.log import EventLog


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    registry = tmp_path / '.datacore/registry/principals.yaml'
    registry.parent.mkdir(parents=True)
    registry.write_text('principals:\n  worker: {kind: agent, writes_as: [runner]}\n  other: {kind: agent, writes_as: [other]}\n')
    monkeypatch.setattr(actor_identity, 'PRINCIPALS', registry)
    space = tmp_path / '1-work'
    return tmp_path, space


def _row(root, now=None):
    return next(r for r in reliability_scoreboard.principal_rows(root, now=now) if r['principal'] == 'worker')


def _append(space, *, actor='runner', log_name=None, etype='metric.attest', **fields):
    return EventLog(space, actor, log_name=log_name).append(etype, {'metric': 'job.verify', 'job': 'check', 'ok': True, 'failures': [], **fields})


def test_run_scoped_attestations_count_for_the_principal(fleet):
    root, space = fleet
    _append(space, log_name='runner-run-2026-09-11')
    assert claim_gate.absent('worker', root=root)[0] is False
    assert _row(root)['ok'] is True


@pytest.mark.parametrize('variant', ['wrong-type', 'truthy-string', 'future', 'invalid-json', 'inconsistent-failure'])
def test_non_evidence_cannot_report_a_verified_principal(fleet, variant):
    root, space = fleet
    _append(space, etype='item.create' if variant == 'wrong-type' else 'metric.attest',
            ok='false' if variant == 'truthy-string' else True,
            failures=['failed'] if variant == 'inconsistent-failure' else [])
    path = space / '.datacore/events/runner.jsonl'
    if variant == 'future':
        e = json.loads(path.read_text())
        e['hlc'] = f'{int((time.time() + 10 * 86400) * 1000):013d}.0000.runner'
        e['hash'] = compute_hash(body_dict(e['seq'], e['hlc'], e['actor'], e['type'], e['payload'], e['prev']))
        path.write_text(json.dumps(e) + '\n')
    if variant == 'invalid-json':
        with path.open('a') as f:
            f.write('{broken}\n')
    assert claim_gate.absent('worker', root=root)[0] is True
    assert _row(root)['ok'] is not True


def test_filename_cannot_assign_another_writers_attestation(fleet):
    root, space = fleet
    _append(space, actor='other')
    (space / '.datacore/events/other.jsonl').rename(space / '.datacore/events/runner.jsonl')
    assert claim_gate.absent('worker', root=root)[0] is True
    assert _row(root)['ok'] is not True


def test_latest_failure_supersedes_earlier_pass_for_the_same_contract(fleet):
    root, space = fleet
    _append(space)
    _append(space, log_name='runner-run-2026-09-11', ok=False, failures=['stale'])
    assert claim_gate.absent('worker', root=root)[0] is True
    assert _row(root)['ok'] is False


def test_zero_now_is_respected_and_future_evidence_does_not_pass(fleet):
    root, space = fleet
    _append(space)
    assert claim_gate.absent('worker', root=root, now=0)[0] is True
    assert _row(root, now=0)['ok'] is not True


@pytest.mark.parametrize('field,value', [('hash', '0' * 64), ('sig', 'ab' * 64), ('hlc', '9' * 400 + '.0000.runner'), ('payload', []), ('payload', {'metric': 'job.verify', 'job': 'check', 'ok': 1, 'failures': []})])
def test_corrupt_or_malformed_attestation_is_explicitly_unverifiable(fleet, field, value):
    root, space = fleet
    _append(space)
    path = space / '.datacore/events/runner.jsonl'
    event = json.loads(path.read_text())
    event[field] = value
    path.write_text(json.dumps(event) + '\n')
    absent, note = claim_gate.absent('worker', root=root)
    assert absent and 'invalid' in note
    row = _row(root)
    assert row['ok'] is None and 'invalid' in row['note']


def test_hlc_counter_and_tied_failure_are_not_lost(fleet):
    root, space = fleet
    _append(space)
    first = space / '.datacore/events/runner.jsonl'
    event = json.loads(first.read_text())
    _append(space, log_name='runner-run-2026-09-11', ok=False, failures=['stale'])
    second = space / '.datacore/events/runner-run-2026-09-11.jsonl'
    failure = json.loads(second.read_text())
    failure['hlc'] = event['hlc']
    failure['hash'] = compute_hash(body_dict(failure['seq'], failure['hlc'], failure['actor'], failure['type'], failure['payload'], failure['prev']))
    second.write_text(json.dumps(failure) + '\n')
    assert _row(root)['ok'] is False
    assert claim_gate.absent('worker', root=root)[0] is True


def test_torn_inflight_tail_does_not_invalidate_a_complete_observation(fleet):
    root, space = fleet
    _append(space)
    with (space / '.datacore/events/runner.jsonl').open('a') as f:
        f.write('{"seq":')
    assert _row(root)['ok'] is True
    assert claim_gate.absent('worker', root=root)[0] is False


@pytest.mark.parametrize('field,value', [('seq', 99), ('seq', 'zero'), ('prev', 'missing-predecessor')])
def test_self_consistent_hash_does_not_excuse_a_broken_chain(fleet, field, value):
    root, space = fleet
    _append(space)
    path = space / '.datacore/events/runner.jsonl'
    event = json.loads(path.read_text())
    event[field] = value
    event['hash'] = compute_hash(body_dict(event['seq'], event['hlc'], event['actor'], event['type'], event['payload'], event['prev']))
    path.write_text(json.dumps(event) + '\n')
    assert claim_gate.absent('worker', root=root)[0] is True
    assert _row(root)['ok'] is None
