"""Scheduled entrypoints bind code to their installation and preserve failures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

LIB = Path(__file__).resolve().parents[1]


def run_job(tmp_path, script, fail='', python=None):
    installed = tmp_path / 'installed code'
    stale = tmp_path / 'data/.datacore/lib'
    for path in (installed, stale):
        (path / 'detectors').mkdir(parents=True)
    shutil.copyfile(LIB / script, installed / script)
    if (LIB / 'runtime_shell.sh').exists():
        shutil.copyfile(LIB / 'runtime_shell.sh', installed / 'runtime_shell.sh')
    trace = tmp_path / 'trace.jsonl'
    names = ['ledger_ingest_org.py', 'shadow_check.py', 'ledger_checkpoint.py',
             'v2_verify.py', 'session_archive.py', 'session_learning_sweep.py',
             'detectors/config_drift.py']
    for name in names:
        (installed / name).write_text('''import json,os,sys
from pathlib import Path
call=[Path(__file__).name,*sys.argv[1:]]
with Path(os.environ['AUDIT_TRACE']).open('a') as output:
    output.write(json.dumps(call)+'\\n')
raise SystemExit(19 if ' '.join(call)==os.environ['AUDIT_FAIL'] else 0)
''')
        (stale / name).write_text('''import os
from pathlib import Path
Path(os.environ['AUDIT_STALE']).write_text('stale code executed')
raise SystemExit(0)
''')
    env = dict(os.environ, DATACORE_ROOT=str(tmp_path / 'data'),
               DATACORE_STATE=str(tmp_path / 'state'), DATACORE_PYTHON=python or sys.executable,
               DATACORE_SWEEP_CAFFEINATED='1', AUDIT_TRACE=str(trace),
               AUDIT_STALE=str(tmp_path / 'stale-ran'), AUDIT_FAIL=fail)
    proc = subprocess.run(['bash', str(installed / script)], env=env,
                          capture_output=True, text=True, timeout=20)
    calls = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    return proc, calls


@pytest.mark.parametrize('script,first', [
    ('ledger_ingest_hourly.sh', 'ledger_ingest_org.py'),
    ('ledger_daily.sh', 'shadow_check.py'),
    ('v2_verify_run.sh', 'v2_verify.py'),
    ('session_learning_daily.sh', 'session_archive.py'),
    ('config_drift_run.sh', 'config_drift.py'),
])
def test_schedule_uses_installed_code_not_data_checkout(tmp_path, script, first):
    proc, calls = run_job(tmp_path, script)
    assert not (tmp_path / 'stale-ran').exists(), 'executed data-checkout code'
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls and calls[0][0] == first
    assert (tmp_path / 'state').stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('failure', ['ledger_checkpoint.py write', 'ledger_checkpoint.py verify'])
def test_daily_backup_failure_cannot_report_success(tmp_path, failure):
    proc, calls = run_job(tmp_path, 'ledger_daily.sh', fail=failure)
    assert proc.returncode != 0
    if failure.endswith('write'):
        assert ['ledger_checkpoint.py', 'verify'] not in calls, 'must not verify an older backup as this run'


def test_failed_archive_cannot_be_acknowledged_by_learning_sweep(tmp_path):
    proc, calls = run_job(tmp_path, 'session_learning_daily.sh',
                           fail='session_archive.py --backfill 2 --status pending')
    assert proc.returncode != 0
    assert not any(call[0] == 'session_learning_sweep.py' for call in calls)


@pytest.mark.parametrize('script', ['ledger_daily.sh', 'ledger_ingest_hourly.sh',
                                  'v2_verify_run.sh', 'session_learning_daily.sh',
                                  'config_drift_run.sh', 'ledger_phase1_cycle.sh'])
def test_explicit_unavailable_runtime_is_not_silently_replaced(tmp_path, script):
    proc, calls = run_job(tmp_path, script, python=str(tmp_path / 'missing-python'))
    assert proc.returncode != 0
    assert calls == []
    assert not (tmp_path / 'stale-ran').exists()


@pytest.mark.parametrize('script,failure', [
    ('ledger_daily.sh', 'shadow_check.py'),
    ('ledger_ingest_hourly.sh', 'ledger_ingest_org.py'),
    ('session_learning_daily.sh', 'session_learning_sweep.py --backlog'),
    ('session_learning_daily.sh', 'session_learning_sweep.py --status'),
    ('v2_verify_run.sh', 'v2_verify.py'),
])
def test_later_success_does_not_erase_a_stage_failure(tmp_path, script, failure):
    proc, calls = run_job(tmp_path, script, fail=failure)
    assert proc.returncode == 19, proc.stdout + proc.stderr
    assert failure.split() in calls
