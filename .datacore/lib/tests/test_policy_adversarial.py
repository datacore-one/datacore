"""Policy checks must bind authority to the writer and cover every log."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import actor_identity
import claim_gate
from ledger.log import EventLog, read_events
from ledger.policy import Policy, PolicyError, guarded_append


@pytest.fixture
def policy(tmp_path, monkeypatch):
    registry = tmp_path / "principals.yaml"
    registry.write_text("principals:\n  human: {kind: human, writes_as: [human]}\n  agent: {kind: agent, writes_as: [worker, alias]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    return Policy("human", frozenset({"email.send"}), principals={"agent": {"may_delegate_to": ["agent"], "max_creates_per_day": 2}})


def test_requester_impersonation_does_not_expand_writer_authority(policy):
    ok, _ = claim_gate.check_create("worker", {"requested_by": "human", "assignee": "elsewhere"}, policy)
    assert not ok


@pytest.mark.parametrize("hops", [-1, 1.9, True, "3.9"])
def test_malformed_hop_count_is_rejected(policy, hops):
    assert not claim_gate.check_create("worker", {"hops": hops}, policy)[0]


def test_daily_limit_counts_aliases_and_branch_logs(tmp_path, policy):
    space = tmp_path / "space"
    for actor in ["worker", "alias"]:
        EventLog(space, actor, sign=False, log_name=actor + "-run").append("item.create", {"id": actor})
    assert not claim_gate.check_create("worker", {"id": "extra"}, policy, space)[0]


def test_limits_use_principal_identity_for_run_aliases(tmp_path, policy):
    for actor in ['worker', 'alias-run-2026-09-10']:
        log = EventLog(tmp_path, actor, sign=False)
        log.append('item.create', {'id': actor})
        log.append('spend.record', {'cents': 100})
    assert not claim_gate.check_create('worker', {'id': 'extra'}, policy, tmp_path)[0]
    assert claim_gate.month_to_date_cents(tmp_path, ['worker']) == 200


def test_budget_cannot_be_reduced_by_negative_spend_and_counts_run_logs(tmp_path):
    log = EventLog(tmp_path, "worker", sign=False, log_name="worker-run")
    log.append("spend.record", {"cents": 100})
    log.append("spend.record", {"cents": -1000})
    assert claim_gate.month_to_date_cents(tmp_path, ["worker"]) == 100


def test_unsigned_grant_cannot_authorize_a_signed_operation(tmp_path):
    grant = EventLog(tmp_path, "human", sign=False).append("approval.grant", {"item": "one"})
    log = EventLog(tmp_path, "worker", sign=True, keys_dir=tmp_path / "keys", registry_path=tmp_path / "registry.yaml")
    with pytest.raises(PolicyError, match="signature"):
        guarded_append(log, "item.create", {"id": "one", "effects": ["email.send"], "approval_ref": grant.hash},
                       Policy("human", frozenset({"email.send"})))


def test_approval_from_another_space_cannot_authorize_write(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    grant = EventLog(first, "human", sign=False).append("approval.grant", {"item": "one"})
    with pytest.raises(PolicyError, match="space"):
        guarded_append(EventLog(second, "worker", sign=False), "item.create",
                       {"id": "one", "effects": ["email.send"], "approval_ref": grant.hash},
                       Policy("human", frozenset({"email.send"})), space_dir=first)


def test_concurrent_creations_share_one_principal_limit(tmp_path, policy):
    def create(number):
        log = EventLog(tmp_path, "worker", sign=False, log_name=f"worker-run-{number}")
        try:
            guarded_append(log, "item.create", {"id": str(number)}, policy)
            return True
        except PolicyError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(create, range(8))) == 2
    assert len(read_events(tmp_path)) == 2


def test_arbitration_read_failure_cannot_authorize_override(tmp_path, policy, monkeypatch):
    import importlib
    log_module = importlib.import_module('ledger.log')
    def broken(*args):
        raise OSError('unreadable owner log')
    monkeypatch.setattr(log_module, 'read_events', broken)
    assert not claim_gate.check_override('worker', 'one', tmp_path, policy)[0]


def test_action_details_cannot_substitute_the_target(tmp_path):
    from briefing.actions import act
    with pytest.raises(ValueError, match='another item'):
        act(tmp_path, 'original', 'dismiss', 'worker', detail={'id': 'victim'})
    assert read_events(tmp_path) == []


@pytest.mark.parametrize('changed', [{'effects': ['prod.deploy']}, {'title': 'changed target'},
    {'check': 'different command'}, {'assignee': 'somebody-else'}])
def test_approval_cannot_authorize_changed_proposal(tmp_path, changed):
    from ledger.policy import approval_payload_hash
    payload = {'id': 'one', 'title': 'approved target', 'effects': ['email.send']}
    grant = EventLog(tmp_path, 'human', sign=False).append('approval.grant',
        {'item': 'one', 'payload_hash': approval_payload_hash(payload)})
    changed_payload = {**payload, **changed, 'approval_ref': grant.hash}
    with pytest.raises(PolicyError, match='payload'):
        guarded_append(EventLog(tmp_path, 'worker', sign=False), 'item.create', changed_payload,
            Policy('human', frozenset({'email.send', 'prod.deploy'})))


def test_simultaneous_claims_only_authorize_one_execution(tmp_path, policy):
    EventLog(tmp_path, 'human', sign=False).append('item.create', {'id': 'one'})
    def claim(number):
        try:
            guarded_append(EventLog(tmp_path, 'worker', sign=False, log_name=f'run-{number}'),
                'item.claim', {'id': 'one'}, policy=policy)
            return True
        except PolicyError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(8))) == 1


def test_personal_cosign_rules_are_enforced_at_creation(tmp_path, policy):
    local_policy = Policy('human', frozenset(), known_effects=frozenset({'email.send'}),
        principals={'agent': {'cosign_effects': ['email.send']}})
    with pytest.raises(PolicyError, match='cosign'):
        guarded_append(EventLog(tmp_path, 'worker', sign=False), 'item.create',
            {'id': 'one', 'effects': ['email.send']}, policy=local_policy)


def _approved_task(space, policy, payload):
    from ledger.policy import approval_payload_hash
    grant = guarded_append(EventLog(space, 'human', sign=False), 'approval.grant',
                           {'item': payload['id'], 'payload_hash': approval_payload_hash(payload)}, policy)
    return {**payload, 'approval_ref': grant.hash}


@pytest.mark.parametrize('change', [{'title': 'different recipient'}, {'effects': []},
                                  {'check': 'different executable'}, {'assignee': 'alias'}])
def test_approved_content_update_needs_new_grant(tmp_path, policy, change):
    task = _approved_task(tmp_path, policy, {'id': 'one', 'title': 'approved', 'effects': ['email.send']})
    log = EventLog(tmp_path, 'worker', sign=False)
    guarded_append(log, 'item.create', task, policy)
    with pytest.raises(PolicyError, match='bind|approval'):
        guarded_append(log, 'item.update', {'id': 'one', **change}, policy)
    assert not any(e.type == 'item.update' for e in read_events(tmp_path))


@pytest.mark.parametrize('change', [{'title': 'different recipient'}, {'effects': [], 'approval_ref': None},
                                  {'effects': ['unknown.effect']}])
def test_claim_revalidates_imported_updates(tmp_path, policy, change):
    task = _approved_task(tmp_path, policy, {'id': 'one', 'title': 'approved', 'effects': ['email.send']})
    log = EventLog(tmp_path, 'worker', sign=False)
    guarded_append(log, 'item.create', task, policy)
    log.append('item.update', {'id': 'one', **change})  # historical/imported low-level write
    with pytest.raises(PolicyError, match='bind|approval|unknown'):
        guarded_append(log, 'item.claim', {'id': 'one'}, policy)


def test_new_content_grant_authorizes_update_and_claim(tmp_path, policy):
    log = EventLog(tmp_path, 'worker', sign=False)
    task = _approved_task(tmp_path, policy, {'id': 'one', 'title': 'original', 'effects': ['email.send']})
    guarded_append(log, 'item.create', task, policy)
    amended = _approved_task(tmp_path, policy, {**task, 'title': 'amended'})
    guarded_append(log, 'item.update', amended, policy)
    guarded_append(log, 'item.claim', {'id': 'one'}, policy)
    with pytest.raises(PolicyError, match='execution has started'):
        guarded_append(log, 'item.update', {'id': 'one', 'title': 'changed in flight'}, policy)


def test_worker_cannot_mint_approval(tmp_path, policy):
    with pytest.raises(PolicyError, match='only the approver'):
        guarded_append(EventLog(tmp_path, 'worker', sign=False), 'approval.grant', {'item': 'one'}, policy)


def test_stale_dispatch_selection_cannot_claim_a_changed_payload(tmp_path, policy):
    from ledger.policy import approval_payload_hash
    log = EventLog(tmp_path, 'worker', sign=False)
    original = {'id': 'one', 'title': 'original'}
    guarded_append(log, 'item.create', original, policy)
    guarded_append(log, 'item.update', {'id': 'one', 'title': 'changed'}, policy)
    with pytest.raises(PolicyError, match='changed after dispatch'):
        guarded_append(log, 'item.claim', {'id': 'one', 'payload_hash': approval_payload_hash(original)}, policy)
    claim = guarded_append(log, 'item.claim', {'id': 'one'}, policy)
    assert claim.payload['payload_hash'] == approval_payload_hash({**original, 'title': 'changed'})


def test_executor_refuses_payload_changed_after_claim(tmp_path, policy, monkeypatch):
    from executors.claude_code import ClaudeCodeExecutor
    log = EventLog(tmp_path, 'worker', sign=False)
    guarded_append(log, 'item.create', {'id': 'one', 'title': 'original'}, policy)
    guarded_append(log, 'item.claim', {'id': 'one'}, policy)
    log.append('item.update', {'id': 'one', 'title': 'changed by import'})
    executor = ClaudeCodeExecutor()
    executor._actor, executor._space, executor._item = 'worker', tmp_path, 'one'
    with pytest.raises(PolicyError, match='claimed payload'):
        executor._execution_env()
