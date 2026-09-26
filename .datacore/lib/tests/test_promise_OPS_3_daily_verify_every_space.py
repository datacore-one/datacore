"""OPS-3 (audit C8, catalogue OI-05 / LS-16): the daily ledger history check
covers every space and alerts on a bad record in any of them.

Seeded failure: the box's daily verify runs `ledger_cli.py verify --space ~/Data`
-- the root's gitignored telemetry dir ("OK 2 files 1734 events") -- so the
~82k space events are never checked and the job is green by construction.
ledger_daily.sh must verify each space that carries .datacore/events (the
v2_verify.spaces() discovery), report per space, and exit non-zero when any
space fails.

Run against a disposable root with stub ledger_cli / shadow_check / checkpoint.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

LIB = Path(__file__).resolve().parents[1]

LEDGER_CLI = '''import os, sys
from pathlib import Path
space = Path(sys.argv[sys.argv.index('--space') + 1])
with (Path(os.environ['AUDIT_DIR']) / 'verified').open('a') as f: f.write(str(space) + '\\n')
if space.name == os.environ.get('AUDIT_BAD', ''):
    print('bridge.jsonl: seq 41: prev hash mismatch', file=sys.stderr)
    sys.exit(1)
print(f'OK 3 files 120 events')
'''


def _daily(tmp_path, bad=''):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    root = tmp_path / 'data'
    (root / '.datacore/events').mkdir(parents=True)          # root telemetry: not a space
    for name in ('0-personal', '2-datacore', '5-plur'):
        (root / name / '.datacore/events').mkdir(parents=True)
    (root / '9-noledger').mkdir()                              # a space without a ledger
    state = tmp_path / 'state'
    audit = tmp_path / 'audit'
    audit.mkdir()
    for f in ('ledger_daily.sh', 'runtime_shell.sh', 'spaces.py', 'yaml_safety.py'):
        shutil.copyfile(LIB / f, scripts / f)
    (scripts / 'ledger_cli.py').write_text(LEDGER_CLI)
    (scripts / 'shadow_check.py').write_text("print('drift 0')\n")
    (scripts / 'ledger_checkpoint.py').write_text("print('OK checkpoint')\n")
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_DIR=str(audit), AUDIT_BAD=bad)
    proc = subprocess.run(['bash', str(scripts / 'ledger_daily.sh')], env=env,
                          capture_output=True, text=True, timeout=60)
    v = audit / 'verified'
    verified = [Path(l).name for l in v.read_text().splitlines()] if v.exists() else []
    return proc, state, root, verified


def test_every_space_with_a_ledger_is_verified(tmp_path):
    proc, state, root, verified = _daily(tmp_path)
    assert sorted(verified) == ['0-personal', '2-datacore', '5-plur'], proc.stdout + proc.stderr
    assert 'data' not in verified, 'the root telemetry dir is not the ledger'
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = proc.stdout + (state / 'ledger-verify.log').read_text() if (state / 'ledger-verify.log').exists() else proc.stdout
    for name in ('0-personal', '2-datacore', '5-plur'):
        assert name in report, f'per-space result for {name}:\n{report}'


def test_one_bad_space_fails_the_daily_check_and_is_named(tmp_path):
    proc, state, root, verified = _daily(tmp_path, bad='2-datacore')
    assert proc.returncode != 0, 'a bad record in any space fails the job'
    assert sorted(verified) == ['0-personal', '2-datacore', '5-plur'], 'one failure does not stop the rest'
    log = (state / 'ledger-verify.log').read_text()
    assert any('2-datacore' in l and 'FAIL' in l for l in log.splitlines()), log
    assert 'prev hash mismatch' in log, 'the reason is kept'
    # The manifest contract matches `^OK ` in MULTILINE mode: a failing run must
    # carry no line starting with "OK ", or the alert would stay green.
    assert not any(l.startswith('OK ') for l in log.splitlines()), log


def test_a_clean_run_writes_an_ok_summary(tmp_path):
    proc, state, root, verified = _daily(tmp_path)
    log = (state / 'ledger-verify.log').read_text()
    assert log.splitlines()[-1].startswith('OK '), log
