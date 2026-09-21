"""A saved decision is scoped to its reviewed board and unchanged inputs."""
import argparse
import hashlib
import json
from pathlib import Path

import pytest
import gtd_decision_board as board


def fixture(tmp_path, monkeypatch):
    source = tmp_path / 'inbox.org'
    source.write_text('* TODO Reviewed task\nSCHEDULED: <2026-09-10 Thu> DEADLINE: <2026-09-30 Wed>\n:PROPERTIES:\n:ID: fixture-id\n:END:\nKeep body\n')
    output = tmp_path / 'review.html'
    row = {'id': 'I1', 'title': 'Reviewed task', 'options': [{'value': 'next'}],
           'apply': {'orgId': 'fixture-id', 'file': str(source), 'state': 'TODO', 'defer': '2026-10-01'}}
    data = {'meta': {'slug': 'review', 'build': 'fixture-build', 'schema': 2,
                     'inputs': {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}},
            'sections': [{'key': 'inbox', 'rows': [row]}]}
    data['meta']['authority'] = board._authority(data['meta']['inputs'])
    data['meta']['build'] = board._build_id(data)
    output.write_text(board._json_block('data', data))
    saved = tmp_path / 'choices.json'
    decisions = {'board': 'review', 'build': data['meta']['build'], 'decisions': {'I1': {'choice': 'next', 'note': ''}}}
    saved.write_text(json.dumps(decisions))
    args = argparse.Namespace(board=str(output), decisions=str(saved), today='2026-09-12',
                              someday_parent=None, dry_run=False, show=25)
    return source, output, saved, args, data, decisions


@pytest.mark.parametrize('variant', ['missing-build', 'wrong-board', 'unknown-choice', 'unknown-row', 'wrong-shape', 'stale-source'])
def test_invalid_decision_never_calls_mutation(tmp_path, monkeypatch, variant):
    source, output, saved, args, data, decisions = fixture(tmp_path, monkeypatch)
    if variant == 'missing-build': decisions.pop('build')
    elif variant == 'wrong-board': decisions['board'] = 'some-other-board'
    elif variant == 'unknown-choice': decisions['decisions']['I1']['choice'] = 'drop'
    elif variant == 'unknown-row': decisions['decisions']['XX'] = decisions['decisions'].pop('I1')
    elif variant == 'wrong-shape': decisions['decisions']['I1'] = ['next']
    else: source.write_text(source.read_text() + '* TODO Later task\n')
    saved.write_text(json.dumps(decisions))
    calls = []
    monkeypatch.setattr(board, '_adapter', lambda op: calls.append(op) or {'updated': True})
    try: board.apply_cmd(args)
    except (ValueError, SystemExit, TypeError, AttributeError): pass
    assert calls == []
    assert not output.with_suffix('.applied.json').exists()


def test_nonzero_empty_adapter_is_never_success(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 9, '', ''))
    try: result = board._adapter(['update', '--file', '/missing-test-only', '--id', 'missing', '--state', 'NEXT'])
    except (ValueError, OSError): return
    assert result.get('error')


def test_duplicate_row_ids_are_refused(tmp_path, monkeypatch):
    _, output, _, _, data, _ = fixture(tmp_path, monkeypatch)
    data['sections'].append({'key': 'extra', 'rows': data['sections'][0]['rows']})
    output.write_text(board._json_block('data', data))
    with pytest.raises(ValueError): board._board_rows(output)


def test_failed_adapter_does_not_record_success(tmp_path, monkeypatch):
    source, output, _, args, _, _ = fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    monkeypatch.setattr(board, '_adapter', lambda op: {})
    try: board.apply_cmd(args)
    except (ValueError, SystemExit): pass
    receipt = json.loads(output.with_suffix('.applied.json').read_text())
    assert not receipt.get('I1') and not receipt.get('completed', {}).get('I1')
    assert source.read_bytes() == before


def change_choice(output, saved, data, decisions, choice):
    data['sections'][0]['rows'][0]['options'] = [{'value': choice}]
    data['meta']['build'] = board._build_id(data)
    output.write_text(board._json_block('data', data))
    decisions['build'] = data['meta']['build']
    decisions['decisions']['I1']['choice'] = choice
    saved.write_text(json.dumps(decisions))


def test_apply_and_retry_preserve_body_and_one_mutation(tmp_path, monkeypatch):
    source, output, _, args, _, _ = fixture(tmp_path, monkeypatch)
    board.apply_cmd(args)
    after = source.read_bytes()
    assert '* NEXT Reviewed task' in after.decode() and 'Keep body' in after.decode()
    assert 'SCHEDULED:' not in after.decode() and 'DEADLINE: <2026-09-30 Wed>' in after.decode()
    board.apply_cmd(args)
    assert source.read_bytes() == after
    receipt = json.loads(output.with_suffix('.applied.json').read_text())
    assert list(receipt['completed']) == ['I1'] and receipt['pending'] is None
    assert output.with_suffix('.applied.json').stat().st_mode & 0o777 == 0o600


def test_simultaneous_apply_runs_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    source, _, _, args, _, _ = fixture(tmp_path, monkeypatch)
    calls = []
    original = board._adapter
    def run(op):
        calls.append(op)
        return original(op)
    monkeypatch.setattr(board, '_adapter', run)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: board.apply_cmd(args), range(8)))
    assert len(calls) == 1 and source.read_text().count(':ID: fixture-id') == 1


def test_failed_someday_update_recovers_move_and_blocks_replay(tmp_path, monkeypatch):
    source, output, saved, args, data, decisions = fixture(tmp_path, monkeypatch)
    change_choice(output, saved, data, decisions, 'someday')
    target = source.with_name('someday.org')
    target.write_text('* Notes\nOriginal target body\n')
    before, target_before = source.read_bytes(), target.read_bytes()
    original = board._adapter
    calls = []
    def fail_second(op):
        calls.append(op)
        return {'error': 'synthetic'} if op[0] == 'update' else original(op)
    monkeypatch.setattr(board, '_adapter', fail_second)
    with pytest.raises(ValueError, match='confirm'): board.apply_cmd(args)
    assert source.read_bytes() == before and target.read_bytes() == target_before
    assert json.loads(output.with_suffix('.applied.json').read_text())['pending']['id'] == 'I1'
    with pytest.raises(ValueError, match='interrupted'): board.apply_cmd(args)
    assert len(calls) == 2


def test_successful_someday_preserves_existing_target_and_retry(tmp_path, monkeypatch):
    source, output, saved, args, data, decisions = fixture(tmp_path, monkeypatch)
    change_choice(output, saved, data, decisions, 'someday')
    target = source.with_name('someday.org')
    target.write_text('* Existing heading\nPreserve destination body\n')
    board.apply_cmd(args)
    assert ':ID: fixture-id' not in source.read_text()
    text = target.read_text()
    assert 'Preserve destination body' in text and 'Keep body' in text and '* TODO Reviewed task' in text
    board.apply_cmd(args)
    assert target.read_text() == text


def test_external_edit_after_success_refuses_stale_revision(tmp_path, monkeypatch):
    source, _, _, args, _, _ = fixture(tmp_path, monkeypatch)
    board.apply_cmd(args)
    source.write_text(source.read_text().replace('NEXT Reviewed', 'DONE Reviewed'))
    before = source.read_bytes()
    with pytest.raises(ValueError, match='changed'): board.apply_cmd(args)
    assert source.read_bytes() == before


def test_crash_after_mutation_restores_org_and_blocks_blind_retry(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    source, output, _, args, _, _ = fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    script = '''
import os,sys,argparse,json
sys.path.insert(0,sys.argv[1])
import gtd_decision_board as b
original=b.write_org_text
def crash(path,text):
 if str(path).endswith('.applied.json'):os._exit(49)
 return original(path,text)
b.write_org_text=crash
b.apply_cmd(argparse.Namespace(**json.loads(sys.argv[2])))
'''
    result = subprocess.run([sys.executable, '-c', script, str(Path(board.__file__).parent), json.dumps(vars(args))],
                            env=dict(os.environ), capture_output=True, text=True, timeout=15)
    assert result.returncode == 49, result.stderr
    with pytest.raises(ValueError, match='interrupted'): board.apply_cmd(args)
    assert source.read_bytes() == before
    assert json.loads(output.with_suffix('.applied.json').read_text())['pending']


def build_args(source, output):
    return argparse.Namespace(today='2026-09-12', slug='review', title='Review </title><script>bad()</script>',
        redact=[], overrides=None, week=None, prefill=None, files=[str(source)], projects=[], states=None,
        min_age=None, eyebrow=None, h1=None, lede=None, as_of=None, out=str(output))


def test_build_identity_changes_with_input_and_rejects_old_prefill(tmp_path):
    source, output = tmp_path/'inbox.org', tmp_path/'review.html'
    source.write_text('* TODO Original\n:PROPERTIES:\n:ID: original\n:END:\n')
    args = build_args(source, output)
    board.build(args)
    original, _ = board._board_rows(output)
    board.build(args)
    assert board._board_rows(output)[0]['meta']['build'] == original['meta']['build']
    source.write_text('* TODO Earlier\n:PROPERTIES:\n:ID: earlier\n:END:\n' + source.read_text())
    board.build(args)
    newer, _ = board._board_rows(output)
    assert newer['meta']['build'] != original['meta']['build']
    choices = tmp_path/'prefill.json'
    choices.write_text(json.dumps({'board':'review', 'build':original['meta']['build'], 'decisions':{}}))
    args.prefill = str(choices)
    with pytest.raises(ValueError, match='build'): board.build(args)
    assert board._board_rows(output)[0]['meta']['build'] == newer['meta']['build']


def test_generated_board_is_private_offline_and_cannot_embed_markup(tmp_path):
    source, output = tmp_path/'inbox.org', tmp_path/'private'/'review.html'
    source.write_text('* TODO Malicious </script><img src=https://invalid.example/x>\n')
    board.build(build_args(source, output))
    text = output.read_text()
    assert 'Content-Security-Policy' in text and "default-src 'none'" in text
    assert 'fonts.googleapis.com' not in text
    assert '</script><img' not in text and '<script>bad()' not in text
    assert output.stat().st_mode & 0o777 == 0o600 and output.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('variant', ['symlink', 'hardlink', 'public-directory', 'slug-traversal'])
def test_artifact_path_refusals_preserve_existing_files(tmp_path, variant):
    source, output, victim = tmp_path/'inbox.org', tmp_path/'review.html', tmp_path/'victim'
    source.write_text('* TODO Keep\n');victim.write_text('do not replace')
    args = build_args(source, output)
    if variant == 'symlink': output.symlink_to(victim)
    elif variant == 'hardlink': output.hardlink_to(victim)
    elif variant == 'public-directory': tmp_path.chmod(0o755)
    else: args.slug = '../escape'
    try:
        with pytest.raises(ValueError): board.build(args)
        assert victim.read_text() == 'do not replace'
    finally: tmp_path.chmod(0o700)


def test_renderer_handles_prototype_keys_and_rejects_executable_links(tmp_path):
    import subprocess
    source, output = tmp_path/'inbox.org', tmp_path/'review.html'
    source.write_text('* TODO Keep\n')
    args = build_args(source, output)
    week = tmp_path/'week.json'
    week.write_text(json.dumps({'sections':[{'key':'__proto__', 'prefix':'Q', 'label':'Synthetic', 'rows':[
        {'area':'__proto__', 'title':'Constructor', 'options':[{'value':'constructor', 'label':'Pick'}],
         'suggested':'constructor', 'links':[{'url':'javascript:alert(1)', 'text':'Unsafe'},
                                           {'url':'https://example.invalid/reference', 'text':'Reference'}]}]}]}))
    args.week = str(week)
    board.build(args)
    data, _ = board._board_rows(output)
    check = '''const fs=require('fs'), vm=require('vm');
const data=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const app={innerHTML:'',addEventListener(){}};
const status={textContent:''};
const document={getElementById(id){return id==='data'?{textContent:JSON.stringify(data)}:id==='app'?app:status;}};
const localStorage={getItem(){return null},setItem(){}};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{document,localStorage,URL,setTimeout,clearTimeout});
if(app.innerHTML.includes('href="javascript:')||!app.innerHTML.includes('https://example.invalid/reference')||!app.innerHTML.includes('Constructor'))process.exit(3);
'''
    fixture_path = tmp_path/'data.json';fixture_path.write_text(json.dumps(data))
    result = subprocess.run(['node', '-e', check, str(fixture_path), str(board.ASSETS/'board.js')],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def generated_fixture(tmp_path, monkeypatch):
    from ledger.log import EventLog, read_events
    from ledger.fold import fold
    from ledger.projector import project
    from ledger.projection_state import STATE, base_document
    import actor_identity
    monkeypatch.setattr(actor_identity, 'this_actor', lambda: 'writer')
    space = tmp_path / '9-drill'
    (space / 'org').mkdir(parents=True)
    (space / '.datacore').mkdir()
    (space / '.datacore/ledger-edit-protocol').write_text('1\n')
    log = EventLog(space, 'writer')
    log.append('item.create', {'id': 'one', 'title': 'original title', 'state': 'TODO', 'space': space.name,
        'level': 1, 'tags': [], 'org': {'body': 'original body', 'properties': {}, 'priority': None}})
    text = project(fold(read_events(space)), space=space.name).text
    (space / 'org/next_actions.org').write_text(text)
    (space / STATE).parent.mkdir(parents=True)
    (space / STATE).write_text(base_document(text))
    (space/'.datacore/ledger-phase').write_text('1\n')
    source = space/'org/next_actions.org'
    output = tmp_path/'review.html'
    board.build(build_args(source, output))
    data, rows = board._board_rows(output)
    rid = next(iter(rows))
    choices = {'board':'review', 'build':data['meta']['build'], 'decisions':{rid:{'choice':'someday', 'note':''}}}
    saved = tmp_path/'decisions.json';saved.write_text(json.dumps(choices))
    args = argparse.Namespace(board=str(output), decisions=str(saved), today='2026-09-12', someday_parent=None, dry_run=False, show=25)
    return space, log, source, output, args


def test_generated_benching_has_wake_date_and_survives_projection(tmp_path, monkeypatch):
    from ledger.fold import fold
    from ledger.log import read_events
    from ledger_project_org import project_space
    space, _, source, _, args = generated_fixture(tmp_path, monkeypatch)
    board.apply_cmd(args)
    item = fold(read_events(space)).items['one']
    assert item.payload['state'] == 'DEFERRED' and item.payload['scheduled']
    assert item.status != 'dismissed'
    assert not source.with_name('someday.org').exists()
    assert project_space(space).startswith('generated')
    assert 'DEFERRED original title' in source.read_text() and 'original body' in source.read_text()


def test_new_ledger_state_cannot_be_changed_by_stale_board(tmp_path, monkeypatch):
    from ledger.log import read_events
    space, log, source, output, args = generated_fixture(tmp_path, monkeypatch)
    log.append('item.update', {'id':'one', 'title':'A different reviewed meaning'})
    before, events = source.read_bytes(), read_events(space)
    with pytest.raises(ValueError, match='ledger'): board.apply_cmd(args)
    assert source.read_bytes() == before and read_events(space) == events
    assert not output.with_suffix('.applied.json').exists()


def test_concurrent_ledger_edit_is_fenced_and_pending_is_not_replayed(tmp_path, monkeypatch):
    from ledger.log import EventLog, read_events
    from ledger.fold import fold
    space, _, source, output, args = generated_fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    original = EventLog.append
    def race(self, kind, payload):
        if kind == 'item.update' and '_merge' in payload:
            original(self, 'item.update', {'id':'one', 'state':'NEXT'})
        return original(self, kind, payload)
    monkeypatch.setattr(EventLog, 'append', race)
    with pytest.raises(ValueError, match='concurrent ledger edit'): board.apply_cmd(args)
    assert source.read_bytes() == before
    item = fold(read_events(space)).items['one']
    assert item.payload['state'] == 'NEXT' and item.edit_conflicts
    count = len(read_events(space))
    with pytest.raises(ValueError, match='interrupted'): board.apply_cmd(args)
    assert len(read_events(space)) == count
    assert json.loads(output.with_suffix('.applied.json').read_text())['pending']


def test_generated_divergence_is_not_reviewable_as_current(tmp_path, monkeypatch):
    space, log, source, output, args = generated_fixture(tmp_path, monkeypatch)
    log.append('item.update', {'id':'one', 'title':'Changed remotely'})
    before = output.read_bytes()
    with pytest.raises(ValueError, match='differ'): board.build(build_args(source, output))
    assert output.read_bytes() == before


def test_a_current_file_with_a_header_and_old_closed_work_is_reviewable(tmp_path, monkeypatch):
    """Every real Phase-1 file has both, and the builder refused every one.

    The check compared the file with a bare `project(state)` -- a complete
    replay, no authored header -- while the projector writes a one-day
    retention window under the file's own #+ lines. Measured 2026-09-21: False
    for all four spaces tried, with nothing out of date. The fixture above never
    caught it because it was written with the same bare call.
    """
    import time as _time
    from ledger_project_org import project_space
    space, log, source, output, _ = generated_fixture(tmp_path, monkeypatch)
    # an authored header, as org-mode users keep
    source.write_text('#+TITLE: Drill\n#+TAGS: deep(d) quick(q)\n' + source.read_text())
    # work closed well outside the retention day
    old_ms = int((_time.time() - 5 * 86400) * 1000)
    log.append('item.create', {'id': 'two', 'title': 'finished last week', 'state': 'TODO',
                               'space': space.name, 'level': 1, 'tags': [],
                               'org': {'body': '', 'properties': {}, 'priority': None}})
    log.append('item.update', {'id': 'two', 'state': 'DONE', 'closed_at': old_ms})
    assert project_space(space).startswith('generated')
    text = source.read_text()
    assert text.startswith('#+TITLE: Drill')
    board.build(build_args(source, output))          # used to raise 'differ'
    assert output.exists()
