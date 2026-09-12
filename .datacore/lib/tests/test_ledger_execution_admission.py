"""Replicated ledger reads cannot independently authorize the same delegation."""
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import pytest

import execution_admission as authority
import ledger_claim
from ledger.log import EventLog, read_events

pytestmark = pytest.mark.usefixtures('briefing_principals')


def binding_id(title, space, installation):
    # Wire-format fixture, independently of the consumer's admission routine.
    raw = json.dumps([' '.join(title.lower().split()), space, installation],
                     separators=(',', ':'), ensure_ascii=False).encode()
    return 'delegation-' + hashlib.sha256(raw).hexdigest()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    space = root / '2-example'
    marker = space / '.datacore/config.yaml'
    marker.parent.mkdir(parents=True)
    marker.write_text('space:\n  name: example\n  type: team\n')
    installation = str(uuid.uuid4())
    state = tmp_path / 'authority'
    authority.provision_database(state, installation)
    site = authority.Site(installation, state)
    monkeypatch.setattr(authority, 'load', lambda: site)
    calls = []
    monkeypatch.setattr(ledger_claim, 'run_task', lambda *a, **k: (calls.append(a) or (True, 'synthetic result', {})))
    monkeypatch.setattr(ledger_claim, '_journal', lambda *a: None)
    payload = {'id': binding_id('Observe fixture', 'example', installation),
               'title': 'Observe fixture', 'effects': [], 'assignee': 'worker',
               'execution_installation': installation, 'execution_space': 'example'}
    EventLog(space, 'human', sign=False).append('item.create', payload)
    def run(path=space, execute=True):
        monkeypatch.setattr('sys.argv', ['ledger_claim.py', '--space', str(path), '--actor', 'worker', *(['--execute'] if execute else [])])
        return ledger_claim.main()
    return space, site, payload, calls, run


def test_disconnected_hosts_do_not_both_execute(fixture, tmp_path, monkeypatch):
    space, site, payload, calls, run = fixture
    copy = tmp_path / 'other-host'
    shutil.copytree(space, copy)
    assert run() == 0
    other_id = str(uuid.uuid4())
    authority.provision_database(tmp_path/'other-authority', other_id)
    monkeypatch.setattr(authority, 'load', lambda: authority.Site(other_id, tmp_path/'other-authority'))
    assert run(copy) == 1
    assert len(calls) == 1
    assert [e.type for e in read_events(copy)] == ['item.create']


def test_same_installation_copied_or_renamed_data_cannot_reexecute(fixture, tmp_path):
    space, site, payload, calls, run = fixture
    copy = tmp_path / '9-renamed'
    shutil.copytree(space, copy)
    assert run() == 0
    assert run(copy) == 1
    assert len(calls) == 1


def test_interrupted_worker_cannot_reexecute_from_stale_copy(fixture, tmp_path, monkeypatch):
    space, site, payload, calls, run = fixture
    copy = tmp_path / 'before-start'
    shutil.copytree(space, copy)
    def interrupted(*a, **k):
        calls.append(a)
        raise RuntimeError('synthetic interruption after a possible effect')
    monkeypatch.setattr(ledger_claim, 'run_task', interrupted)
    with pytest.raises(RuntimeError, match='synthetic interruption'):
        run()
    assert run(copy) == 1
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [
    ('execution_installation', None), ('execution_installation', 'invalid'),
    ('execution_space', 'elsewhere'), ('id', 'unbound'),
])
def test_malformed_or_unbound_creation_never_executes(fixture, tmp_path, field, value):
    space, site, payload, calls, run = fixture
    other = tmp_path / 'malformed'
    (other/'.datacore').mkdir(parents=True)
    shutil.copyfile(space/'.datacore/config.yaml', other/'.datacore/config.yaml')
    EventLog(other, 'human', sign=False).append('item.create', {**payload, field: value})
    assert run(other) == 1
    assert calls == []
    assert [e.type for e in read_events(other)] == ['item.create']


def test_retargeted_creation_cannot_reuse_the_delegation_identity(fixture, tmp_path, monkeypatch):
    space, site, payload, calls, run = fixture
    other_id = str(uuid.uuid4())
    authority.provision_database(tmp_path/'other-authority', other_id)
    monkeypatch.setattr(authority, 'load', lambda: authority.Site(other_id, tmp_path/'other-authority'))
    other = tmp_path/'retargeted'
    (other/'.datacore').mkdir(parents=True)
    shutil.copyfile(space/'.datacore/config.yaml', other/'.datacore/config.yaml')
    EventLog(other, 'human', sign=False).append('item.create', {**payload, 'execution_installation': other_id})
    assert run(other) == 1
    assert calls == []


@pytest.mark.parametrize('field,value', [('execution_installation', None), ('execution_space', 'elsewhere')])
def test_guarded_update_cannot_remove_or_retarget_creation_binding(fixture, field, value):
    from ledger.policy import guarded_append, PolicyError
    space, site, payload, calls, run = fixture
    with pytest.raises(PolicyError, match='immutable'):
        guarded_append(EventLog(space, 'human', sign=False), 'item.update', {'id':payload['id'], field:value})
    assert len(read_events(space)) == 1


def test_replicated_unguarded_update_cannot_retarget_execution(fixture, tmp_path, monkeypatch):
    space, site, payload, calls, run = fixture
    other_id = str(uuid.uuid4())
    authority.provision_database(tmp_path/'other-authority', other_id)
    monkeypatch.setattr(authority, 'load', lambda: authority.Site(other_id, tmp_path/'other-authority'))
    EventLog(space, 'human', sign=False).append('item.update', {'id':payload['id'], 'execution_installation':other_id})
    assert run() == 1
    assert calls == []


def test_changed_marker_does_not_create_another_execution_namespace(fixture):
    space, site, payload, calls, run = fixture
    (space/'.datacore/config.yaml').write_text('space: {name: changed, type: team}\n')
    assert run() == 1
    assert calls == []


def test_failed_response_stays_claimed_and_cannot_automatically_retry(fixture, monkeypatch):
    from ledger.fold import fold
    space, site, payload, calls, run = fixture
    def fail(*a, **k):
        calls.append(a)
        return False, 'synthetic provider failure after possible effects', {}
    monkeypatch.setattr(ledger_claim, 'run_task', fail)
    assert run() == 1
    assert fold(read_events(space)).items[payload['id']].status == 'claimed'
    assert not any(e.type == 'item.release' for e in read_events(space))
    assert run() == 0
    assert len(calls) == 1


def test_durable_consumption_survives_interruption_before_return(fixture, tmp_path, monkeypatch):
    space, site, payload, calls, run = fixture
    copy = tmp_path/'stale'
    shutil.copytree(space, copy)
    original = authority.Site.consume
    def interrupted(self, *args):
        original(self, *args)
        raise authority.SiteError('synthetic failure after COMMIT')
    monkeypatch.setattr(authority.Site, 'consume', interrupted)
    assert run() == 1
    monkeypatch.setattr(authority.Site, 'consume', original)
    assert run(copy) == 1
    assert calls == []


def test_claim_write_failure_does_not_return_permission_to_execute(fixture, monkeypatch):
    space, site, payload, calls, run = fixture
    original = EventLog.append
    def failed(self, kind, payload):
        if kind == 'item.claim':
            raise OSError('synthetic append failure')
        return original(self, kind, payload)
    monkeypatch.setattr(EventLog, 'append', failed)
    with pytest.raises(OSError, match='synthetic append failure'):
        run()
    assert calls == []
    monkeypatch.setattr(EventLog, 'append', original)
    assert run() == 0
    assert len(calls) == 1


def test_another_owner_cannot_commit_using_a_stale_receipt(fixture, monkeypatch):
    from ledger.fold import fold
    from ledger_execution import admit, Receipt
    from ledger.policy import PolicyError
    space, site, payload, calls, run = fixture
    receipt = admit(space, fold(read_events(space)).items[payload['id']], 'worker', 'research', 'fixture')
    log = EventLog(space, 'human', sign=False)
    EventLog(space, 'worker', sign=False).append('item.release', {'id':payload['id'], 'owner':'worker'})
    log.append('item.claim', {'id':payload['id'], 'owner':'human'})
    assert fold(read_events(space)).items[payload['id']].owner == 'human'
    before = len(read_events(space))
    # A stale preflight result cannot bypass the policy's locked result check.
    monkeypatch.setattr(Receipt, 'valid', lambda self: True)
    with pytest.raises(PolicyError, match='receipt'):
        receipt.record('item.complete', {'artifact_commit':'synthetic'})
    assert len(read_events(space)) == before


def test_forged_result_token_is_refused_by_the_canonical_policy(fixture):
    from ledger.fold import fold
    from ledger_execution import admit
    from ledger.policy import guarded_append, PolicyError
    space, site, payload, calls, run = fixture
    receipt = admit(space, fold(read_events(space)).items[payload['id']], 'worker', 'research', 'fixture')
    with pytest.raises(PolicyError, match='receipt'):
        guarded_append(EventLog(space, 'worker', sign=False), 'item.complete',
                       {'id':payload['id'], 'owner':'worker', 'execution_token':'forged', 'payload_hash':receipt.payload_hash})
    assert fold(read_events(space)).items[payload['id']].status == 'claimed'


def test_valid_verified_result_completes_once(fixture, monkeypatch):
    from ledger.fold import fold
    space, site, payload, calls, run = fixture
    EventLog(space, 'human', sign=False).append('item.update', {'id':payload['id'], 'check':'synthetic check'})
    monkeypatch.setattr(ledger_claim, '_isolated_check', lambda *a: (True, 'synthetic-commit'))
    assert run() == 0
    assert fold(read_events(space)).items[payload['id']].status == 'completed'
    assert run() == 0
    assert len(calls) == 1
    assert sum(e.type == 'item.complete' for e in read_events(space)) == 1


def test_dry_run_does_not_require_or_consume_an_installation(fixture, monkeypatch):
    space, site, payload, calls, run = fixture
    def unavailable():
        raise AssertionError('planning must not load authority')
    monkeypatch.setattr(authority, 'load', unavailable)
    assert run(execute=False) == 0
    assert calls == []
    assert len(read_events(space)) == 1


def test_materialize_and_briefing_preserve_explicit_execution_binding(fixture, tmp_path):
    from briefing.actions import materialize
    from briefing_materialize import proposals
    from ledger_execution import delegation_id
    space, site, payload, calls, run = fixture
    entries = proposals({'delegate':[{'task':'A distinct observation', 'execution_installation':site.installation,
                                     'assignee':'worker', 'check':'synthetic check'}]})
    result = materialize(entries, space, 'human')
    assert len(result.created) == 1 and result.blocked == []
    created = result.created[0].payload
    assert created['id'] == delegation_id('A distinct observation', 'example', site.installation)
    assert created['execution_space'] == 'example'
    assert created['execution_installation'] == site.installation
    assert created['assignee'] == 'worker' and created['check'] == 'synthetic check'
    assert materialize(entries, space, 'human').created == []
    # Omitting or changing routing does not resurrect that same proposal.
    assert materialize([{'text':'A distinct observation'}], space, 'human').created == []
    assert materialize([{**entries[0], 'execution_installation':str(uuid.uuid4())}], space, 'human').created == []


def test_independent_consumer_processes_share_one_durable_admission(fixture, tmp_path):
    import subprocess
    import sys
    import actor_identity
    import ledger.policy
    space, site, payload, calls, run = fixture
    effect = tmp_path/'effects'
    code = '''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import actor_identity, ledger.policy, ledger_claim, execution_admission as authority
actor_identity.PRINCIPALS=Path(sys.argv[2])
ledger.policy.DEFAULT_POLICY_PATH=Path(sys.argv[3])
authority.load=lambda: authority.Site(sys.argv_saved[4],Path(sys.argv_saved[5]))
def effect(*a,**k):
    with Path(sys.argv_saved[7]).open('a') as f: f.write('executed\\n')
    return True,'synthetic result',{}
ledger_claim.run_task=effect
ledger_claim._journal=lambda *a:None
sys.argv_saved=list(sys.argv)
sys.argv=['ledger_claim.py','--space',sys.argv_saved[6],'--actor','worker','--execute']
raise SystemExit(ledger_claim.main())
'''
    processes = []
    for n in range(6):
        copy = tmp_path/f'copy-{n}'
        shutil.copytree(space, copy)
        processes.append(subprocess.Popen([sys.executable,'-c',code,str(Path(ledger_claim.__file__).parent),
                                          str(actor_identity.PRINCIPALS),str(ledger.policy.DEFAULT_POLICY_PATH),
                                          site.installation,str(site.state),str(copy),str(effect)],
                                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
    results = [(p.communicate(timeout=20),p.returncode) for p in processes]
    assert sorted(code for _,code in results) == [0,1,1,1,1,1], results
    assert effect.read_text().splitlines() == ['executed']


def test_malformed_creation_identity_does_not_break_new_proposals(fixture):
    from briefing.actions import materialize
    space, site, payload, calls, run = fixture
    EventLog(space, 'human', sign=False).append('item.create', {'id':[], 'title':'Malformed historical item'})
    result = materialize([{'text':'Valid new proposal', 'execution_installation':site.installation}], space, 'human')
    assert len(result.created) == 1 and result.blocked == []
