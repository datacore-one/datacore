"""SYN-7 / P0-3 (audit B-F4, B-F13): a sync or converge problem in one space
never stops ingest, projection or sync of the other spaces on that machine.

Seeded failure: restore the whole-cycle abort in ledger_phase1_cycle.sh -- any
non-offline converge failure (`if [ "$rc" -ne 0 ]; then finish ...; exit`)
ends the cycle before ingest, so one space's refused autosave leaves every
space on the host unprojected (69 whole-host aborts on nightshift, 10 on the
box, 2026-09-26: "converge 2-datacore: failed" and then nothing else ran).

Also pinned (B-F13): the cycle log names the failing space with FAILED and its
reason, for a converge failure and for an ingest failure alike, and the status
file still reports FAIL while any space failed.

Run against a disposable root with stub transport / ingest / projector.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

LIB = Path(__file__).resolve().parents[1]

SPACES = ('1-bad', '2-good', '3-good')

TRANSPORT = '''import json, os, sys
from pathlib import Path
space = Path(sys.argv[sys.argv.index('--space') + 1]).name
trace = Path(os.environ['AUDIT_DIR']) / 'transport'
n = sum(1 for l in (trace.read_text().splitlines() if trace.exists() else []) if l == space)
with trace.open('a') as f: f.write(space + '\\n')
fail = os.environ.get('AUDIT_CONVERGE_FAIL', '')
when = os.environ.get('AUDIT_CONVERGE_WHEN', 'receive')
phase = 'receive' if n == 0 else 'publish'
if space == fail and phase == when:
    print(json.dumps({"ok": False, "reason": "autosave refused by pre-commit hook", "context": {}}, indent=2))
    sys.exit(1)
print(json.dumps({"ok": True, "reason": "converged", "context": {}}, indent=2))
'''

INGEST = '''import os, sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index('--root') + 1]) if '--root' in sys.argv else Path(os.environ['DATACORE_ROOT'])
seen = sorted(p.name for p in root.glob('[0-9]-*') if (p / 'org').is_dir())
with (Path(os.environ['AUDIT_DIR']) / 'ingested').open('a') as f: f.write(' '.join(seen) + '\\n')
failing = os.environ.get('AUDIT_INGEST_FAIL', '')
for name in seen:
    if name == failing:
        print(f"{name:14} FAILED: EditConflict: concurrent edit at items.fa66190f")
    else:
        print(f"{name:14} new=   0 closed=  0 updated=  0 known=   3")
print(f"imported 0 task(s) across {len(seen)} space(s); {1 if failing in seen else 0} space(s) failed")
sys.exit(1 if failing in seen else 0)
'''

PROJECT = '''import os, sys
from pathlib import Path
with (Path(os.environ['AUDIT_DIR']) / 'projected').open('a') as f: f.write(sys.argv[sys.argv.index('--space') + 1] + '\\n')
print('generated org/next_actions.org')
'''


def _cycle(tmp_path, **env_extra):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    root = tmp_path / 'data'
    for name in SPACES:
        space = root / name
        (space / '.datacore/events').mkdir(parents=True)
        (space / '.git').mkdir()
        (space / 'org').mkdir()
        (space / '.datacore/ledger-phase').write_text('1\n')
    state = tmp_path / 'state'
    audit = tmp_path / 'audit'
    audit.mkdir()
    shutil.copyfile(LIB / 'ledger_phase1_cycle.sh', scripts / 'cycle.sh')
    shutil.copyfile(LIB / 'runtime_shell.sh', scripts / 'runtime_shell.sh')
    (scripts / 'ledger_transport.py').write_text(TRANSPORT)
    (scripts / 'ledger_ingest_org.py').write_text(INGEST)
    (scripts / 'ledger_project_org.py').write_text(PROJECT)
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_DIR=str(audit), **env_extra)
    proc = subprocess.run(['bash', str(scripts / 'cycle.sh')], env=env,
                          capture_output=True, text=True, timeout=60)
    read = lambda n: (audit / n).read_text() if (audit / n).exists() else ''
    return proc, state, read


def test_one_space_failing_converge_does_not_stop_the_others(tmp_path):
    proc, state, read = _cycle(tmp_path, AUDIT_CONVERGE_FAIL='1-bad')
    out = proc.stdout + proc.stderr
    ingested = read('ingested')
    assert ingested, f'ingest must still run for the healthy spaces:\n{out}'
    swept = ingested.split()
    assert '2-good' in swept and '3-good' in swept, f'healthy spaces are ingested: {swept}\n{out}'
    assert '1-bad' not in swept, 'a space whose converge failed may be mid-merge; never ingest it'
    projected = read('projected').split()
    assert '2-good' in projected and '3-good' in projected, f'healthy spaces are projected:\n{out}'
    assert '1-bad' not in projected, 'a space whose converge failed is never projected over'
    assert (state / 'phase1-cycle-status.txt').read_text().startswith('FAIL'), \
        'a stuck space still fails the cycle'
    assert proc.returncode != 0


def test_the_failing_space_and_its_reason_are_in_the_cycle_log(tmp_path):
    """B-F13: the cycle log (stdout -> phase1-cycle.log) must say which space
    failed and why, not just `failed; see its log` in a file overwritten hourly."""
    proc, _, _ = _cycle(tmp_path, AUDIT_CONVERGE_FAIL='1-bad')
    lines = [l for l in proc.stdout.splitlines() if '1-bad' in l and 'FAILED' in l]
    assert lines, f'no FAILED line for 1-bad:\n{proc.stdout}'
    assert any('autosave refused by pre-commit hook' in l for l in lines), \
        f'the FAILED line carries the reason:\n{proc.stdout}'


def test_an_ingest_failure_reason_reaches_the_cycle_log(tmp_path):
    """B-F13: `ingest rc=1` alone cannot say why 2-datacore failed on 09-23."""
    proc, _, read = _cycle(tmp_path, AUDIT_INGEST_FAIL='3-good')
    assert any('3-good' in l and 'FAILED' in l and 'EditConflict' in l
               for l in proc.stdout.splitlines()), proc.stdout
    assert '2-good' in read('projected').split()


def test_a_publish_converge_failure_skips_only_that_space_projection(tmp_path):
    proc, state, read = _cycle(tmp_path, AUDIT_CONVERGE_FAIL='1-bad', AUDIT_CONVERGE_WHEN='publish')
    projected = read('projected').split()
    assert '2-good' in projected and '3-good' in projected, proc.stdout + proc.stderr
    assert '1-bad' not in projected, 'a space whose publish converge failed is not projected'
    assert (state / 'phase1-cycle-status.txt').read_text().startswith('FAIL')
    assert any('1-bad' in l and 'FAILED' in l for l in proc.stdout.splitlines()), proc.stdout
