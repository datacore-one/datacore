"""Actual child startup must retain only assigned credentials and stdio."""
import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import resource

import pytest

import mcp_stdio as launcher


@pytest.fixture
def configured(tmp_path):
    private = tmp_path.resolve() / 'provider'
    private.mkdir(mode=0o700)
    script = private / 'server.py'
    script.write_text('''import json,os,sys
try:
    os.fstat(int(sys.argv[1])); inherited=True
except OSError:
    inherited=False
print(json.dumps({'assigned':os.getenv('EXAMPLE_ASSIGNED_KEY'),
    'unrelated':os.getenv('EXAMPLE_OTHER_KEY'), 'ssh':os.getenv('SSH_AUTH_SOCK'),
    'node_options':os.getenv('NODE_OPTIONS'), 'pythonpath':os.getenv('PYTHONPATH'),
    'home':os.getenv('HOME'), 'cwd':os.getcwd(), 'descriptor':inherited,
    'prefix':sys.prefix, 'input':sys.stdin.readline().strip()}))
''')
    profile = {'version': 1, 'command': [sys.executable, '-I', str(script), '9999'],
               'home': str(private), 'cwd': str(private), 'environment': {},
               'credential_names': ['EXAMPLE_ASSIGNED_KEY'],
               'credential_sha256': launcher.credential_digest({'EXAMPLE_ASSIGNED_KEY': 'assigned-fixture'})}
    return private, profile


def write_config(private, profile, credentials=None):
    config, secrets = private / 'profile.json', private / 'secrets.json'
    config.write_text(json.dumps(profile))
    secrets.write_text(json.dumps(credentials if credentials is not None else {'EXAMPLE_ASSIGNED_KEY': 'assigned-fixture'}))
    config.chmod(0o600)
    secrets.chmod(0o600)
    return config, secrets


def run_provider(config, secrets, **kwargs):
    return subprocess.run([sys.executable, '-I', launcher.__file__, '--profile', str(config),
                           '--credentials', str(secrets)], capture_output=True, text=True, timeout=10, **kwargs)


def test_actual_child_has_only_assigned_credentials_and_no_extra_descriptor(configured):
    private, profile = configured
    read_fd, write_fd = os.pipe()
    try:
        profile['command'][-1] = str(read_fd)
        config, secrets = write_config(private, profile)
        env = dict(os.environ, EXAMPLE_OTHER_KEY='unrelated-fixture', SSH_AUTH_SOCK='/not/assigned',
                   NODE_OPTIONS='--require=/not/assigned.js', PYTHONPATH='/not/assigned')
        result = run_provider(config, secrets, input='stdio retained\n', env=env, pass_fds=(read_fd,))
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data['assigned'] == 'assigned-fixture'
        assert all(data[key] is None for key in ('unrelated', 'ssh', 'node_options', 'pythonpath'))
        assert data['descriptor'] is False
        assert data['home'] == data['cwd'] == str(private)
        assert data['input'] == 'stdio retained'
        assert data['prefix'] == sys.prefix  # preserving the selected venv is essential
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_lowered_descriptor_limit_does_not_preserve_an_already_open_descriptor(configured):
    private, profile = configured
    read_fd, write_fd = os.pipe()
    high_fd = fcntl.fcntl(read_fd, fcntl.F_DUPFD, 128)
    try:
        profile['command'][-1] = str(high_fd)
        config, secrets = write_config(private, profile)
        result = run_provider(config, secrets, input='', pass_fds=(high_fd,),
                              preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64)))
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['descriptor'] is False
    finally:
        os.close(high_fd)
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize('key', ['PATH', 'HOME', 'NODE_OPTIONS', 'PYTHONPATH', 'BASH_ENV', 'LD_PRELOAD', 'SSH_AUTH_SOCK'])
@pytest.mark.parametrize('source', ['environment', 'credentials'])
def test_explicit_loader_and_identity_injection_refused(configured, key, source):
    _, profile = configured
    secrets = {'EXAMPLE_ASSIGNED_KEY': 'assigned-fixture'}
    if source == 'environment':
        profile['environment'][key] = 'forbidden'
    else:
        secrets[key] = 'forbidden'
        profile['credential_names'].append(key)
    with pytest.raises(ValueError):
        launcher.build_launch(profile, secrets)


@pytest.mark.parametrize('credentials', [{}, {'EXAMPLE_ASSIGNED_KEY': 'x', 'OTHER_KEY': 'y'},
    {'EXAMPLE_ASSIGNED_KEY': None}, {'EXAMPLE_ASSIGNED_KEY': 'x\0y'}])
def test_unexpected_credentials_cannot_start(configured, credentials):
    private, profile = configured
    config, secrets = write_config(private, profile, credentials)
    result = run_provider(config, secrets)
    assert result.returncode == 1
    assert result.stdout == ''
    assert 'validation failed' in result.stderr


def test_interrupted_rotation_cannot_pair_a_profile_with_different_credentials(configured):
    private, profile = configured
    config, secrets = write_config(private, profile)
    secrets.write_text(json.dumps({'EXAMPLE_ASSIGNED_KEY': 'different-account-fixture'}))
    result = run_provider(config, secrets)
    assert result.returncode == 1
    assert result.stdout == ''
    assert 'different-account-fixture' not in result.stderr


def test_matching_rotation_succeeds_and_new_profile_with_old_credentials_refuses(configured):
    private, profile = configured
    updated = {'EXAMPLE_ASSIGNED_KEY': 'rotated-fixture'}
    profile['credential_sha256'] = launcher.credential_digest(updated)
    config, secrets = write_config(private, profile)
    result = run_provider(config, secrets)
    assert result.returncode == 1 and result.stdout == ''
    secrets.write_text(json.dumps(updated, indent=2))
    result = run_provider(config, secrets, input='')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['assigned'] == 'rotated-fixture'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'public', 'parent-public',
                                'parent-link', 'oversize', 'duplicate', 'utf8'])
def test_unsafe_credential_storage_refused_without_disclosure(configured, kind):
    private, profile = configured
    config, secrets = write_config(private, profile)
    if kind in {'symlink', 'hardlink', 'fifo'}:
        saved = private / 'retained'
        secrets.rename(saved)
        if kind == 'symlink': secrets.symlink_to(saved)
        elif kind == 'hardlink': os.link(saved, secrets)
        else: os.mkfifo(secrets)
    elif kind == 'public': secrets.chmod(0o644)
    elif kind == 'parent-public': private.chmod(0o755)
    elif kind == 'parent-link':
        link = private.parent / 'alias'
        link.symlink_to(private, target_is_directory=True)
        secrets = link / secrets.name
    elif kind == 'oversize': secrets.write_text('x' * (launcher.MAX_BYTES + 1))
    elif kind == 'duplicate': secrets.write_text('{"TOKEN":"sensitive-fixture","TOKEN":"second"}')
    else: secrets.write_bytes(b'\xffsensitive-fixture')
    result = run_provider(config, secrets)
    assert result.returncode == 1
    assert result.stdout == ''
    assert 'sensitive-fixture' not in result.stderr


def test_short_reads_preserve_configuration(configured, monkeypatch):
    private, profile = configured
    _, secrets = write_config(private, profile, {'EXAMPLE_ASSIGNED_KEY': '€🙂' * 50})
    original = os.read
    monkeypatch.setattr(os, 'read', lambda fd, count: original(fd, min(count, 7)))
    assert launcher._read_private(secrets)['EXAMPLE_ASSIGNED_KEY'] == '€🙂' * 50


def test_installer_symlink_cannot_avoid_command_validation(configured):
    private, profile = configured
    target = private / 'package-resolver.js'
    target.write_text('#!/bin/sh\nexit 0\n')
    target.chmod(0o700)
    entry = private / 'npx'
    entry.symlink_to(target)
    profile['command'] = [str(entry), '--yes', 'example-provider']
    with pytest.raises(ValueError, match='package acquisition'):
        launcher.build_launch(profile, {'EXAMPLE_ASSIGNED_KEY': 'assigned-fixture'})


@pytest.mark.parametrize('change', ['unknown', 'version', 'relative-command', 'missing-command',
                                   'shell', 'python-without-isolation', 'duplicate-scope', 'overlap'])
def test_bad_profiles_fail_before_provider_execution(configured, change):
    private, profile = configured
    if change == 'unknown': profile['inherit_environment'] = True
    elif change == 'version': profile['version'] = True
    elif change == 'relative-command': profile['command'][0] = 'python3'
    elif change == 'missing-command': profile['command'][0] = str(private / 'missing')
    elif change == 'shell': profile['command'] = ['/bin/sh', '-c', 'exit 0']
    elif change == 'python-without-isolation': profile['command'].remove('-I')
    elif change == 'duplicate-scope': profile['credential_names'] *= 2
    else: profile['environment']['EXAMPLE_ASSIGNED_KEY'] = 'shadow'
    config, secrets = write_config(private, profile)
    result = run_provider(config, secrets)
    assert result.returncode == 1
    assert result.stdout == ''
