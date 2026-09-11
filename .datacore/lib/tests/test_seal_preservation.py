"""Settlement covers exact independent writer logs and all folded fields."""
from copy import deepcopy
from dataclasses import fields
import pytest
from ledger.fold import ItemState, LedgerState
from ledger.log import EventLog, read_events
from ledger.seal import build_seal_payload, verify_seal, settled_events


def test_every_item_field_changes_the_state_root():
    item=ItemState(id='a',title='task',owner='worker',status='claimed')
    state=LedgerState(items={'a':item}); before=state.state_root()
    for field in fields(item):
        changed=deepcopy(state)
        value=getattr(item,field.name)
        setattr(changed.items['a'],field.name, ['different'] if isinstance(value,list) else {'different':True} if isinstance(value,dict) else 'different')
        assert changed.state_root() != before, field.name


def test_new_run_log_never_enters_an_old_seal(tmp_path):
    EventLog(tmp_path,'worker').append('item.create',{'id':'old','title':'old'})
    EventLog(tmp_path,'sealer').append('ledger.seal',build_seal_payload(read_events(tmp_path)))
    EventLog(tmp_path,'worker',log_name='worker-run-later').append('item.create',{'id':'new','title':'new'})
    events=read_events(tmp_path)
    assert verify_seal(events)[0] is True
    assert [e.payload['id'] for e in settled_events(events)] == ['old']


def test_missing_nonfolded_event_invalidates_seal_and_cannot_be_settled(tmp_path):
    EventLog(tmp_path,'worker').append('approval.grant',{'id':'grant'})
    EventLog(tmp_path,'worker').append('item.create',{'id':'item','title':'task'})
    EventLog(tmp_path,'sealer').append('ledger.seal',build_seal_payload(read_events(tmp_path)))
    events=[e for e in read_events(tmp_path) if e.type != 'approval.grant']
    assert verify_seal(events)[0] is False
    with pytest.raises(ValueError,match='unverified'):
        settled_events(events)


def test_legacy_seal_remains_readable_but_requires_resealing(tmp_path):
    EventLog(tmp_path,'worker').append('item.create',{'id':'item','title':'task'})
    payload=build_seal_payload(read_events(tmp_path)); payload.pop('version');payload.pop('event_set_hash')
    EventLog(tmp_path,'sealer').append('ledger.seal',payload)
    assert verify_seal(read_events(tmp_path))[0] is None
    assert 'legacy' in verify_seal(read_events(tmp_path))[1]
    EventLog(tmp_path,'sealer').append('ledger.seal',build_seal_payload(read_events(tmp_path)))
    assert verify_seal(read_events(tmp_path))[0] is True


@pytest.mark.parametrize('watermarks',[None,[],{'worker':True},{'worker':-1},{'worker':'1'}])
def test_malformed_frontier_cannot_attest_state(tmp_path,watermarks):
    EventLog(tmp_path,'sealer').append('ledger.seal',{'version':2,'watermarks':watermarks,'state_root':'x'})
    assert verify_seal(read_events(tmp_path))[0] is False


def test_sealer_cannot_certify_a_history_with_a_missing_prefix(tmp_path):
    EventLog(tmp_path,'worker').append('approval.grant',{'id':'grant'})
    EventLog(tmp_path,'worker').append('item.create',{'id':'item','title':'task'})
    incomplete=read_events(tmp_path)[1:]
    with pytest.raises(ValueError,match='incomplete'):
        build_seal_payload(incomplete)


def test_same_writer_can_emit_work_after_sealing_and_old_seals_are_not_folded(tmp_path):
    log=EventLog(tmp_path,'worker')
    log.append('item.create',{'id':'item','title':'task'})
    log.append('ledger.seal',build_seal_payload(read_events(tmp_path)))
    log.append('approval.grant',{'id':'grant'})
    log.append('ledger.seal',build_seal_payload(read_events(tmp_path)))
    assert verify_seal(read_events(tmp_path))[0] is True
    assert [event.type for event in settled_events(read_events(tmp_path))] == ['item.create','approval.grant']
