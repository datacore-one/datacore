"""A login or credit failure pauses this host's claiming instead of retrying.

Infrastructure failures are deliberately not counted as attempts (a sick host
must not dismiss good work), so before this a dead key was retried every 15
minutes forever: plur-claw from 2026-09-28 to 2026-09-30, each run leaking a
108 MB temp dir until the disk filled. Guardrail: a login or usage limit is
reported, then we wait. The host reports once, keeps items queued, and probes
again only after a quiet interval; the first success clears the pause.
"""
from datetime import datetime, timedelta, timezone

import pytest

import claim_backoff as B

OBSERVED = ("openclaw: openclaw execution failed (exit 2): {'message': 'stream disconnected "
            "before completion: You have no credits remaining. Add credits to continue'}")


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path))
    return tmp_path


@pytest.mark.parametrize('detail', [
    OBSERVED,
    'claude-code auth rejected (\'not logged in\'): please run /login',
    'api: 401 Unauthorized',
    'openrouter: insufficient_quota',
    'hermes: credit balance is too low',
])
def test_access_failures_are_recognised(detail):
    assert B.is_access_failure(detail)


@pytest.mark.parametrize('detail', [
    'check failed: pytest -q',
    'openclaw: openclaw execution failed (exit 2): tool error in step 3',
    'rate limit exceeded, retry in 20s',     # transient: ordinary retry handles it
    'claude-code timed out after 600s',
])
def test_other_failures_are_not(detail):
    assert not B.is_access_failure(detail)


def test_first_block_reports_once_and_pauses():
    now = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    assert B.status('data', now) == (False, None)
    assert B.record_failure('data', OBSERVED, now) is True       # new: report it
    assert B.record_failure('data', OBSERVED, now) is False      # already reported
    paused, reason = B.status('data', now + timedelta(minutes=15))
    assert paused and 'no credits remaining' in reason


def test_probe_is_allowed_after_the_quiet_interval():
    now = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    B.record_failure('data', OBSERVED, now)
    assert B.status('data', now + B.PROBE_AFTER - timedelta(minutes=1))[0] is True
    assert B.status('data', now + B.PROBE_AFTER)[0] is False
    # a failed probe restarts the quiet interval without a second report
    assert B.record_failure('data', OBSERVED, now + B.PROBE_AFTER) is False
    assert B.status('data', now + B.PROBE_AFTER + timedelta(minutes=15))[0] is True


def test_success_clears_the_pause_and_blocks_are_per_actor():
    now = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    B.record_failure('data', OBSERVED, now)
    assert B.status('winston', now) == (False, None)
    B.clear('data')
    assert B.status('data', now) == (False, None)


def test_unreadable_block_file_keeps_the_host_paused(state):
    now = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    B.record_failure('data', OBSERVED, now)
    next(state.glob('claim-access-block-data.json')).write_text('{not json')
    paused, reason = B.status('data', now)
    assert paused and 'unreadable' in reason


# ── wired into the real dispatcher ────────────────────────────────────────────

from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet, _events  # noqa: E402,F401


def test_dispatcher_pauses_on_a_credit_failure_and_keeps_the_work(fleet, tmp_path, monkeypatch):  # noqa: F811
    import ledger_claim
    from ledger_claim import MAX_ATTEMPTS
    root, space, drill = fleet
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    calls = []

    def dead_key(title, route, cwd, item_id='', hops=0, actor=''):
        calls.append(item_id)
        return False, OBSERVED, {}
    monkeypatch.setattr(ledger_claim, 'run_task', dead_key)
    a = drill.delegate(space, by='winston', to='miles', id='a' * 16, title='write A into a.txt', check='test -f a.txt')
    b = drill.delegate(space, by='winston', to='miles', id='b' * 16, title='write B into b.txt', check='test -f b.txt')

    first = drill.dispatch(space, 'miles')
    assert len(calls) == 1, f'kept running work after a credit failure: {first[:400]}'
    assert 'claiming paused' in first
    later = [drill.dispatch(space, 'miles') for _ in range(MAX_ATTEMPTS + 2)]
    assert len(calls) == 1, 'a paused host claimed again before its probe interval'
    assert all('PAUSED' in o for o in later), later[-1][:300]
    for iid in (a, b):
        assert drill.item(space, iid).status != 'dismissed'   # the work is kept, not given up

    B.clear('miles')                                          # login fixed / owner resumes
    monkeypatch.setattr(ledger_claim, 'run_task', lambda *a, **k: (calls.append('ok') or (False, 'check failed: x', {})))
    resumed = drill.dispatch(space, 'miles')
    assert 'PAUSED' not in resumed and len(calls) > 1
