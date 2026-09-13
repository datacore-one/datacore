"""A data checkout cannot replace an installed hook's code or subprocesses."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

LIB = Path(__file__).resolve().parents[1]


def environment(tmp_path):
    return {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path),
            'DATACORE_ROOT': str(tmp_path / 'Data'),
            'DATACORE_STATE': str(tmp_path / 'state')}


@pytest.mark.parametrize('hook', ['session_time_guardian.py', 'session_cleanup.py',
                                 'session_firstmsg.py', 'session_bootstrap.py', 'active_memory.py'])
def test_hook_uses_installed_helpers_and_preserves_selected_data_root(tmp_path, hook):
    installed = tmp_path / 'installed';installed.mkdir()
    for name in [hook, 'session_state.py', 'file_utils.py', 'plur_cli.py', 'process_run.py']:
        shutil.copy2(LIB / name, installed / name)
    stale = tmp_path / 'Data/.datacore/lib';stale.mkdir(parents=True)
    (stale / 'session_state.py').write_text("raise RuntimeError('DATA_CODE_SELECTED')\n")
    code = '''import json,runpy,sys
from pathlib import Path
runpy.run_path(sys.argv[1],run_name='fixture')
import session_state
session_state.create_session('Preserved fixture input','fixture')
assert session_state.read_session('fixture')['first_prompt']=='Preserved fixture input'
print(json.dumps({'source':session_state.__file__,'state':session_state._state_file('fixture')}))
'''
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(installed / hook)],
                       env=environment(tmp_path), capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    assert Path(result['source']) == installed / 'session_state.py'
    assert Path(result['state']).is_relative_to(tmp_path / 'Data/.datacore/state')


def test_wrap_up_runs_its_installed_archive_helpers(tmp_path):
    installed = tmp_path / 'installed';installed.mkdir()
    shutil.copy2(LIB / 'wrap_up_mechanics.py', installed / 'wrap_up_mechanics.py')
    stale = tmp_path / 'Data/.datacore/lib';stale.mkdir(parents=True)
    trace = tmp_path / 'executions'
    for directory, origin in [(installed, 'installed'), (stale, 'data')]:
        for name in ['nightshift_archival.py', 'session_archive.py']:
            (directory / name).write_text('from pathlib import Path\n'
                f'with Path({str(trace)!r}).open("a") as f:f.write({origin!r}+"\\n")\n'
                'print(\'{"status":"ok"}\')\n')
    code = '''import runpy,sys
ns=runpy.run_path(sys.argv[1],run_name='fixture')
g=ns['cmd_preflight'].__globals__
g.update(scan_processes=lambda:{'kill':[]},kill_processes=lambda *a:[],
         context_sync_check=lambda:{},artifact_scan=lambda:{},repo_status=lambda:{})
result=ns['cmd_preflight'](dry_run=True)
assert result['nightshift_archival']['ok']
assert result['session_archive']['status']=='ok'
'''
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(installed / 'wrap_up_mechanics.py')],
                       env=environment(tmp_path), capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    assert trace.read_text().splitlines() == ['installed', 'installed']


@pytest.mark.parametrize('missing', [False, True])
def test_job_runner_cannot_replace_missing_installed_process_control(tmp_path, missing):
    installed = tmp_path / 'installed';(installed / 'jobs').mkdir(parents=True)
    shutil.copy2(LIB / 'jobs/run.py', installed / 'jobs/run.py')
    stale = tmp_path / 'Data/.datacore/lib';stale.mkdir(parents=True)
    (stale / 'process_run.py').write_text("def run(*a,**k):return 'data'\n")
    if not missing:(installed / 'process_run.py').write_text("def run(*a,**k):return 'installed'\n")
    code = "import runpy,sys;ns=runpy.run_path(sys.argv[1],run_name='fixture');print(ns['run_process']())"
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(installed / 'jobs/run.py')],
                       env=environment(tmp_path), capture_output=True, text=True, timeout=10)
    if missing:
        assert p.returncode != 0 and 'data' not in p.stdout
    else:
        assert p.returncode == 0, p.stderr
        assert p.stdout.strip() == 'installed'


def test_repair_command_uses_installed_task_gate(tmp_path):
    installed = tmp_path / 'installed';installed.mkdir()
    shutil.copy2(LIB / 'undelegate_unexecutable.py', installed / 'undelegate_unexecutable.py')
    (installed / 'org_transaction.py').write_text('def serialized(f):return f\nclass SafeOrgWorkspace:pass\n')
    (installed / 'ai_task_gate.py').write_text('DONE_KEYS=()\nQUEUED=()\n')
    stale = tmp_path / 'Data/.datacore/lib';stale.mkdir(parents=True)
    (stale / 'ai_task_gate.py').write_text("raise RuntimeError('DATA_CODE_SELECTED')\n")
    p = subprocess.run([sys.executable, '-B', str(installed / 'undelegate_unexecutable.py'), 'fixture-space'],
                       env=environment(tmp_path), capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr


def test_stream_tailer_uses_installed_outbox_and_transport(tmp_path):
    installed = tmp_path / 'installed/lib';installed.mkdir(parents=True)
    shutil.copy2(LIB / 'agent_stream_tail.py', installed / 'agent_stream_tail.py')
    stale = tmp_path / 'Data/.datacore/lib';stale.mkdir(parents=True)
    for directory, origin in [(installed, 'installed'), (stale, 'data')]:
        (directory / 'file_utils.py').write_text('def atomic_write_text(*a):pass\ndef file_lock(*a):pass\n')
        (directory / 'agent_outbox.py').write_text(f'def enqueue(*a):return {origin!r}\ndef acknowledge(*a):pass\ndef flush(*a):pass\n')
        (directory / 'relay_client.py').write_text('def post_event(*a):pass\n')
    code = "import runpy,sys;ns=runpy.run_path(sys.argv[1],run_name='fixture');print(ns['enqueue']())"
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(installed / 'agent_stream_tail.py')],
                       env=environment(tmp_path), capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == 'installed'


@pytest.mark.parametrize('broken', [False, True])
def test_review_date_helper_is_installed_and_failure_preserves_document(tmp_path, broken):
    installed = tmp_path / 'installed/lib';installed.mkdir(parents=True)
    for name in ['intent_review.py', 'file_utils.py']:
        shutil.copy2(LIB / name, installed / name)
    stale = tmp_path / 'Data/.datacore/lib';stale.mkdir(parents=True)
    (stale / 'date_utils.py').write_text("print('1900-01-01')\n")
    (installed / 'date_utils.py').write_text('raise SystemExit(17)\n' if broken else "print('2030-01-02')\n")
    key = hashlib.sha256(str((tmp_path / 'Data').resolve()).encode()).hexdigest()
    directory = tmp_path / 'state'
    for component in ('', 'intent-reviews', key):
        if component:
            directory /= component
        directory.mkdir(mode=0o700)
    output = directory / 'review.md';output.write_text('Original review\n');output.chmod(0o600)
    code = '''import runpy,sys
ns=runpy.run_path(sys.argv[1],run_name='fixture')
g=ns['main'].__globals__;g['build']=lambda root,today:'Date: '+today+'\\n'
sys.argv=['intent_review','--root',sys.argv[2],'--out','review.md']
raise SystemExit(ns['main']())
'''
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(installed / 'intent_review.py'), str(tmp_path / 'Data')],
                       env=environment(tmp_path), capture_output=True, text=True, timeout=10)
    if broken:
        assert p.returncode != 0 and output.read_text() == 'Original review\n'
    else:
        assert p.returncode == 0, p.stderr
        assert output.read_text() == 'Date: 2030-01-02\n'
