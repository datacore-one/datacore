"""An OpenClaw host runs delegated items through the Gateway, never `agent exec`.

`agent exec` reads the environment's pay-per-use key (drained on plur-claw) and
leaves a ~108 MB state directory in /tmp on every run. From 2026-09-28 a claim
retrying every 15 minutes through it filled plur-claw's disk (46.9 GB in 436
directories). The gateway executor existed since 2026-09-23 but the host was
never switched: the installer declared `openclaw` and its verify step accepted it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]


def _host(tmp_path, identity):
    home = tmp_path / 'home'
    runner = home / '.datacore/v2-runner'
    lib = runner / '.datacore/lib'
    registry = runner / '.datacore/registry'
    lib.mkdir(parents=True); registry.mkdir()
    registry.joinpath('infrastructure.yaml').write_text(
        'servers:\n  claw:\n    setup_profile: openclaw\n    access: {actor: fixture}\n')
    home.joinpath('.datacore').mkdir(exist_ok=True)
    home.joinpath('.datacore/identity.env').write_text(identity)
    lib.joinpath('actor_identity.py').write_text(
        'from pathlib import Path\n'
        f'REGISTRY_DIR = Path({str(registry)!r})\n'
        'if __name__ == "__main__":\n    print("fixture identity.env")\n')
    shutil.copyfile(LIB / 'cron_install.py', lib / 'cron_install.py')
    bindir = tmp_path / 'bin'; bindir.mkdir()
    bindir.joinpath('python3').symlink_to(sys.executable)
    for name, body in (('systemctl', 'exit 0'),
                       ('crontab', 'case "$1" in -l) cat "$TEST_CRONTAB" ;; -) cat > "$TEST_CRONTAB" ;; *) exit 2 ;; esac')):
        bindir.joinpath(name).write_text(f'#!/bin/sh\n{body}\n')
        bindir.joinpath(name).chmod(0o755)
    store = tmp_path / 'crontab'; store.write_text('')
    env = {**os.environ, 'HOME': str(home), 'DATACORE_RUNNER': str(runner),
           'DATACORE_STATE': str(home / '.datacore/state'), 'TEST_CRONTAB': str(store),
           'PATH': str(bindir) + os.pathsep + os.environ['PATH']}
    command = ['bash', str(LIB / 'agent_host_setup.sh'), '--host', 'claw']
    return home / '.datacore/identity.env', command, env


def _executor_lines(id_file):
    return [l for l in id_file.read_text().splitlines() if l.startswith('DATACORE_EXECUTOR=')]


def test_new_openclaw_host_is_declared_on_the_gateway(tmp_path):
    id_file, command, env = _host(tmp_path, 'DATACORE_ACTOR=fixture\n')
    subprocess.run(command, env=env, capture_output=True, text=True)
    assert _executor_lines(id_file) == ['DATACORE_EXECUTOR=openclaw-gateway']


def test_stale_agent_exec_declaration_is_replaced_not_kept(tmp_path):
    id_file, command, env = _host(tmp_path, 'DATACORE_ACTOR=fixture\nDATACORE_EXECUTOR=openclaw\nKEEP=me\n')
    subprocess.run(command, env=env, capture_output=True, text=True)
    assert _executor_lines(id_file) == ['DATACORE_EXECUTOR=openclaw-gateway']
    assert 'KEEP=me' in id_file.read_text()


def test_verify_fails_a_host_still_on_agent_exec(tmp_path):
    id_file, command, env = _host(tmp_path, 'DATACORE_ACTOR=fixture\nDATACORE_EXECUTOR=openclaw\n')
    checked = subprocess.run([*command, '--verify'], env=env, capture_output=True, text=True)
    assert 'FAIL executor' in checked.stdout
    assert 'OK  executor declared' not in checked.stdout
    assert _executor_lines(id_file) == ['DATACORE_EXECUTOR=openclaw']  # verify changes nothing
