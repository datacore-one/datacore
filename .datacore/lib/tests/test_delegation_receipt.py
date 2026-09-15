"""Freshness requires a matching complete attempt, including crash/retry cases."""
import json
from datetime import datetime, timezone

import pytest

from delegation_receipt import fresh_hours, paths, publish


@pytest.fixture
def receipt(tmp_path):
    config = tmp_path / 'teams/control/.datacore/config.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('space: {name: datacore, type: team}\n')
    success, attempt = paths(tmp_path)
    record = {'version': 2, 'status': 'complete', 'actor': 'reviewer',
              'review_id': 'first', 'ts': datetime.now(timezone.utc).isoformat()}
    publish(tmp_path, success, record)
    publish(tmp_path, attempt, record)
    assert fresh_hours(tmp_path) is not None
    return tmp_path, success, attempt, record


@pytest.mark.parametrize('status', ['running', 'failed', 'complete'])
def test_new_attempt_cannot_reuse_prior_success(receipt, status):
    root, success, attempt, record = receipt
    old = success.read_bytes()
    publish(root, attempt, dict(record, review_id='second', status=status))
    assert fresh_hours(root) is None
    assert success.read_bytes() == old


def test_crash_between_completion_writes_holds_work_and_retry_recovers(receipt):
    root, success, attempt, record = receipt
    publish(root, attempt, dict(record, review_id='second', status='running'))
    publish(root, success, dict(record, review_id='second'))
    assert fresh_hours(root) is None
    publish(root, attempt, dict(record, review_id='second'))
    assert fresh_hours(root) is not None


@pytest.mark.parametrize('content', [None, '{', '[]', '{"version":2,"version":2}',
                                    '{"version":1,"status":"complete"}'])
def test_missing_malformed_or_legacy_attempt_is_not_success(receipt, content):
    root, _, attempt, _ = receipt
    if content is None:
        attempt.unlink()
    else:
        attempt.write_text(content)
    assert fresh_hours(root) is None


def test_concurrent_attempt_change_is_observed(receipt, monkeypatch):
    import delegation_receipt as module
    root, _, attempt, record = receipt
    original = module.read_text_within
    def changing(root, path, **kwargs):
        value = original(root, path, **kwargs)
        if path == attempt:
            attempt.write_text(json.dumps(dict(record, status='running')))
        return value
    monkeypatch.setattr(module, 'read_text_within', changing)
    assert fresh_hours(root) is None
