"""Exercise the scheduled shell entrypoint with failing disposable stages."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

LIB = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fail_stage,expected', [
    ('receive', ['transport']),
    ('ingest', ['transport', 'ingest']),
    ('publish', ['transport', 'ingest', 'transport']),
    ('project', ['transport', 'ingest', 'transport', 'project']),
    ('', ['transport', 'ingest', 'transport', 'project']),
])
def test_failure_never_reaches_a_dependent_write(tmp_path, fail_stage, expected):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    root = tmp_path / 'data'
    space = root / '9-test space'
    (space / '.datacore/events').mkdir(parents=True)
    (space / '.git').mkdir()
    (space / '.datacore/ledger-phase').write_text('1\n')
    source = space / 'next_actions.org'
    source.write_text('new edited content that only ingest can preserve\n')
    trace = tmp_path / 'trace'
    state = tmp_path / 'state'
    shutil.copyfile(LIB / 'ledger_phase1_cycle.sh', scripts / 'cycle.sh')
    shutil.copyfile(LIB / 'runtime_shell.sh', scripts / 'runtime_shell.sh')
    stub = '''import os,sys,json
from pathlib import Path
trace=Path(os.environ['AUDIT_TRACE'])
lines=trace.read_text().splitlines() if trace.exists() else []
name=Path(sys.argv[0]).stem
stage='transport' if name=='ledger_transport' else ('ingest' if name=='ledger_ingest_org' else 'project')
with trace.open('a') as f:f.write(json.dumps([stage,sys.argv[1:]])+'\\n')
fail=os.environ['AUDIT_FAIL']
actual='receive' if stage=='transport' and not lines else ('publish' if stage=='transport' else stage)
if actual==fail:sys.exit(17)
print('stage succeeded')
'''
    for name in ['ledger_transport', 'ledger_ingest_org', 'ledger_project_org']:
        (scripts / f'{name}.py').write_text(stub)
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_TRACE=str(trace), AUDIT_FAIL=fail_stage)
    proc = subprocess.run(['bash', str(scripts / 'cycle.sh')], env=env,
                          capture_output=True, text=True, timeout=15)
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [entry[0] for entry in calls] == expected, proc.stdout + proc.stderr
    assert bool(proc.returncode) == bool(fail_stage)
    assert (state / 'phase1-cycle-status.txt').read_text().startswith('FAIL' if fail_stage else 'OK')
    assert source.read_text() == 'new edited content that only ingest can preserve\n'
    for stage, args in calls:
        if stage == 'transport':
            assert args[-1] == str(space), 'paths containing spaces remain one argument'


@pytest.mark.parametrize('reason,ok', [
    ('fetch failed (offline?)', True),
    ('auth denied (key rejected — check the key, or a VPN/exit node)', False),
    ('remote repo missing', False),
])
def test_an_asleep_laptop_is_not_a_failed_cycle(tmp_path, reason, ok):
    """The mac is a laptop. Its 02:53Z cycle on 2026-09-18 met a sleeping
    network -- two fetches timed out against the Gitea host -- wrote FAIL, and
    alerted about a machine that was asleep. ledger_transport already says
    offline is a condition and denied is a fault; this caller now reads it."""
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    root = tmp_path / 'data'
    space = root / '9-test'
    (space / '.datacore/events').mkdir(parents=True)
    (space / '.git').mkdir()
    (space / '.datacore/ledger-phase').write_text('1\n')
    state = tmp_path / 'state'
    shutil.copyfile(LIB / 'ledger_phase1_cycle.sh', scripts / 'cycle.sh')
    shutil.copyfile(LIB / 'runtime_shell.sh', scripts / 'runtime_shell.sh')
    stub = '''import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).stem
if name == 'ledger_transport':
    print(json.dumps({"ok": False, "reason": os.environ['AUDIT_REASON'], "context": {}}))
    sys.exit(1)
print('stage succeeded')
'''
    for name in ['ledger_transport', 'ledger_ingest_org', 'ledger_project_org']:
        (scripts / f'{name}.py').write_text(stub)
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_REASON=reason)
    proc = subprocess.run(['bash', str(scripts / 'cycle.sh')], env=env,
                          capture_output=True, text=True, timeout=30)

    status = (state / 'phase1-cycle-status.txt').read_text()
    assert status.startswith('OK' if ok else 'FAIL'), proc.stdout + proc.stderr
    assert (proc.returncode == 0) is ok
    if ok:
        assert 'offline' in proc.stdout, 'an offline cycle still says so out loud'
