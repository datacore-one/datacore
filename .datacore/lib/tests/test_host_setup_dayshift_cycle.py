"""The owner's session server runs the phase-1 cycle hourly, like the box.

Until 2026-10-28 dayshift had no schedule at all: no task lists were projected,
its copies fell hundreds of commits behind, and /continue there found none of
the 192 open continuation tasks. The installer owns every cron a host needs, so
the dayshift profile installs the cycle.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]


def test_dayshift_profile_installs_the_hourly_cycle(tmp_path):
    home = tmp_path / 'home'
    runner = home / 'Data'
    lib = runner / '.datacore/lib'
    registry = runner / '.datacore/registry'
    lib.mkdir(parents=True); registry.mkdir()
    registry.joinpath('infrastructure.yaml').write_text(
        'servers:\n  dayshift:\n    access: {actor: dayshift}\n')
    home.joinpath('.datacore').mkdir(exist_ok=True)
    home.joinpath('.datacore/identity.env').write_text('DATACORE_ACTOR=dayshift\n')
    lib.joinpath('actor_identity.py').write_text(
        'from pathlib import Path\n'
        f'REGISTRY_DIR = Path({str(registry)!r})\n'
        'if __name__ == "__main__":\n    print("dayshift fixture")\n')
    shutil.copyfile(LIB / 'cron_install.py', lib / 'cron_install.py')
    bindir = tmp_path / 'bin'; bindir.mkdir()
    bindir.joinpath('python3').symlink_to(sys.executable)
    for name, body in (('systemctl', 'exit 0'), ('sudo', 'exit 1'),
                       ('crontab', 'case "$1" in -l) cat "$TEST_CRONTAB" ;; -) cat > "$TEST_CRONTAB" ;; *) exit 2 ;; esac')):
        bindir.joinpath(name).write_text(f'#!/bin/sh\n{body}\n')
        bindir.joinpath(name).chmod(0o755)
    store = tmp_path / 'crontab'; store.write_text('')
    env = {**os.environ, 'HOME': str(home), 'DATACORE_RUNNER': str(runner),
           'DATACORE_STATE': str(home / '.datacore/state'), 'TEST_CRONTAB': str(store),
           'PATH': str(bindir) + os.pathsep + os.environ['PATH']}
    subprocess.run(['bash', str(LIB / 'agent_host_setup.sh'), '--host', 'dayshift'],
                   env=env, capture_output=True, text=True)
    cycle = [l for l in store.read_text().splitlines() if 'ledger_phase1_cycle.sh' in l]
    assert len(cycle) == 1
    assert cycle[0].startswith('25 * * * * DATACORE_ROOT=')
