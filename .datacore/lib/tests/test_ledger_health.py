"""Runtime health must account for every discovered ledger and uncertainty."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import ledger_health as health
from ledger.log import EventLog


def space(root, name):
    target = root/name
    (target/'.datacore').mkdir(parents=True, exist_ok=True)
    (target/'.datacore/config.yaml').write_text('space:\n  name: fixture\n  type: team\n')
    EventLog(target, 'fixture', sign=False).append('item.create', {'id':str(name), 'title':'Synthetic'})
    return target


def test_all_supported_space_locations_are_verified(tmp_path):
    for name in ('.', '12-legacy', 'named', 'group/nested'): space(tmp_path, name)
    (tmp_path/'12-legacy/.datacore/config.yaml').unlink()
    result = health.check(tmp_path)
    assert result['ok'] is True and result['spaces_verified'] == 4


def test_corrupt_chain_is_reported_without_its_private_contents(tmp_path):
    root = space(tmp_path, '0-fixture')
    path = root/'.datacore/events/fixture.jsonl'
    path.write_text(path.read_text().replace('Synthetic', 'PRIVATE FIXTURE ONLY'))
    result = health.check(tmp_path)
    assert result['ok'] is False and result['spaces_broken'] == 1
    assert 'PRIVATE FIXTURE' not in json.dumps(result)


def test_one_unverifiable_space_prevents_success(tmp_path, monkeypatch):
    good, other = space(tmp_path, '0-good'), space(tmp_path, '1-other')
    original = health._verify_file
    def fail(path, registry):
        if path.is_relative_to(other): raise PermissionError('private diagnostics')
        return original(path, registry)
    monkeypatch.setattr(health, '_verify_file', fail)
    result = health.check(tmp_path)
    assert result['ok'] is None and result['spaces_verified'] == 1 and result['spaces_unverified'] == 1
    assert 'private diagnostics' not in json.dumps(result)


def test_busy_writer_is_unverified_not_broken(tmp_path):
    root = space(tmp_path, '0-fixture')
    with (root/'.datacore/events/fixture.jsonl').open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = health.check(tmp_path)
    assert result['ok'] is None and result['spaces_broken'] == 0
    assert health.check(tmp_path)['ok'] is True


def test_witness_for_absent_log_cannot_prove_health(tmp_path):
    root = space(tmp_path, '0-fixture')
    (root/'.datacore/events/fixture.jsonl').unlink()
    result = health.check(tmp_path)
    assert result['ok'] is None and result['spaces_unverified'] == 1


def test_changed_log_during_check_is_unverified(tmp_path, monkeypatch):
    root = space(tmp_path, '0-fixture')
    before = (root/'.datacore/events/fixture.jsonl').read_bytes()
    original = health.verify_chain
    def race(path, **kwargs):
        result = original(path, **kwargs)
        path.write_bytes(before + b'\n')
        return result
    monkeypatch.setattr(health, 'verify_chain', race)
    assert health.check(tmp_path)['ok'] is None


@pytest.mark.parametrize('kind', ['invalid-marker', 'aliased-ledger'])
def test_incomplete_discovery_or_alias_refuses_success(tmp_path, kind):
    root = space(tmp_path, '0-fixture')
    if kind == 'invalid-marker': (root/'.datacore/config.yaml').write_text('space: [wrong]\n')
    else:
        events = root/'.datacore/events'
        events.rename(root/'.datacore/real-events')
        events.symlink_to(root/'.datacore/real-events', target_is_directory=True)
    assert health.check(tmp_path)['ok'] is None


def test_no_ledger_is_not_a_health_pass(tmp_path):
    assert health.check(tmp_path)['ok'] is None


def test_signed_chain_uses_selected_installations_public_registry(tmp_path, monkeypatch):
    import ledger.keys
    root = space(tmp_path, '0-fixture')
    registry = tmp_path/'.datacore/keys/registry.yaml'
    EventLog(root, 'signed-fixture', sign=True, keys_dir=tmp_path/'private-keys', registry_path=registry).append(
        'item.create', {'id':'signed', 'title':'Synthetic signed data'})
    monkeypatch.setattr(ledger.keys, 'DEFAULT_REGISTRY_PATH', tmp_path/'wrong-registry.yaml')
    assert health.check(tmp_path)['ok'] is True
    registry.rename(tmp_path/'preserved-registry.yaml')
    assert health.check(tmp_path)['ok'] is None


def test_invalid_witness_is_unverified_without_claiming_chain_damage(tmp_path):
    root = space(tmp_path, '0-fixture')
    (root/'.datacore/state/seq-hwm/fixture.seq').write_text('invalid')
    result = health.check(tmp_path)
    assert result['ok'] is None and result['spaces_broken'] == 0


def test_installed_cli_ignores_ambient_python_startup_and_working_directory(tmp_path):
    space(tmp_path, 'named')
    startup = tmp_path/'startup'
    startup.mkdir()
    marker = tmp_path/'startup-ran'
    (startup/'sitecustomize.py').write_text(f'open({str(marker)!r}, "w").write("unexpected")\n')
    result = subprocess.run(
        [sys.executable, '-I', str(Path(health.__file__).resolve()), '--root', str(tmp_path)],
        cwd=startup, env={**os.environ, 'PYTHONPATH': str(startup)},
        text=True, capture_output=True, timeout=15, check=True)
    assert json.loads(result.stdout)['ok'] is True
    assert not marker.exists()
