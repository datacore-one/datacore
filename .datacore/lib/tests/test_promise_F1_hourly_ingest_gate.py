"""F1 remainder (audit B-F1): the box's standalone hourly ingest never sweeps
a Phase-1 space whose last converge failed, and never overlaps a phase-1 cycle.

Seeded failure: ledger_ingest_hourly.sh as it was -- `ledger_ingest_org.py`
over the whole root with no converge gate and no cycle lock. While the cycle
was aborted on 2026-09-26 05:25-08:25Z, the `:00` ingest kept running the
orphan sweep against a stale projection and dismissed two fresh 5-plur tasks
as `housekeeping` (terminal under DIP-0034).

Run against a disposable root with stub transport / ingest / projector: a
phase-1 cycle in which 1-bad's converge fails, then the hourly ingest.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

LIB = Path(__file__).resolve().parents[1]

TRANSPORT = '''import json, sys
from pathlib import Path
space = Path(sys.argv[sys.argv.index('--space') + 1]).name
if space == '1-bad':
    print(json.dumps({"ok": False, "reason": "autosave refused by pre-commit hook", "context": {}}))
    sys.exit(1)
print(json.dumps({"ok": True, "reason": "converged", "context": {}}))
'''

INGEST = '''import os, sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index('--root') + 1]) if '--root' in sys.argv else Path(os.environ['DATACORE_ROOT'])
seen = sorted(p.name for p in root.glob('[0-9]-*') if (p / 'org').is_dir())
with (Path(os.environ['AUDIT_DIR']) / 'ingested').open('a') as f: f.write(' '.join(seen) + '\\n')
for name in seen: print(f"{name:14} new=   0 closed=  0 updated=  0 known=   3")
print(f"imported 0 task(s) across {len(seen)} space(s); 0 space(s) failed")
'''


def _setup(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    root = tmp_path / 'data'
    for name in ('1-bad', '2-good'):
        space = root / name
        (space / '.datacore/events').mkdir(parents=True)
        (space / '.git').mkdir()
        (space / 'org').mkdir()
        (space / '.datacore/ledger-phase').write_text('1\n')
    state = tmp_path / 'state'
    audit = tmp_path / 'audit'
    audit.mkdir()
    for f in ('ledger_phase1_cycle.sh', 'ledger_ingest_hourly.sh', 'runtime_shell.sh'):
        shutil.copyfile(LIB / f, scripts / f)
    (scripts / 'ledger_transport.py').write_text(TRANSPORT)
    (scripts / 'ledger_ingest_org.py').write_text(INGEST)
    (scripts / 'ledger_project_org.py').write_text("print('generated')\n")
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_DIR=str(audit))
    run = lambda script: subprocess.run(['bash', str(scripts / script)], env=env,
                                        capture_output=True, text=True, timeout=60)
    ingested = audit / 'ingested'
    return run, state, ingested


def test_hourly_ingest_skips_a_phase1_space_whose_last_converge_failed(tmp_path):
    run, state, ingested = _setup(tmp_path)
    run('ledger_phase1_cycle.sh')
    ingested.unlink(missing_ok=True)

    proc = run('ledger_ingest_hourly.sh')
    swept = ingested.read_text().split() if ingested.exists() else []
    assert '2-good' in swept, f'the healthy space is still ingested hourly:\n{proc.stdout}{proc.stderr}'
    assert '1-bad' not in swept, \
        'a Phase-1 space whose last converge failed must not be swept against its stale projection'
    assert '1-bad' in proc.stdout, 'the skipped space is named in the cron log'


def test_hourly_ingest_resumes_a_space_once_its_converge_succeeds(tmp_path):
    run, state, ingested = _setup(tmp_path)
    run('ledger_phase1_cycle.sh')
    # The operator fixes 1-bad; the next cycle converges it cleanly.
    t = tmp_path / 'scripts' / 'ledger_transport.py'
    t.write_text(t.read_text().replace("== '1-bad'", "== 'nobody'"))
    run('ledger_phase1_cycle.sh')
    ingested.unlink(missing_ok=True)
    run('ledger_ingest_hourly.sh')
    assert '1-bad' in ingested.read_text().split()


def test_hourly_ingest_leaves_a_running_cycle_alone(tmp_path):
    run, state, ingested = _setup(tmp_path)
    lock = state / 'phase1-cycle.lock'
    lock.mkdir(parents=True)
    (lock / 'pid').write_text(str(os.getpid()))          # a process that IS alive
    proc = run('ledger_ingest_hourly.sh')
    assert not ingested.exists(), 'no ingest while a phase-1 cycle holds the lock'
    assert proc.returncode == 0, 'losing the race is not a failure'
    assert lock.exists() and (lock / 'pid').read_text() == str(os.getpid()), \
        "a live owner's lock is not taken from it"


def test_hourly_ingest_releases_the_cycle_lock(tmp_path):
    run, state, ingested = _setup(tmp_path)
    proc = run('ledger_ingest_hourly.sh')
    assert ingested.exists(), proc.stdout + proc.stderr
    assert not (state / 'phase1-cycle.lock').exists(), 'the lock is released when the ingest ends'
