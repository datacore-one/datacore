"""Worker startup preserves explicit credential scope and refuses unsafe state."""
import json
import os
from types import SimpleNamespace

import pytest

import runtime_context as runtime


@pytest.fixture
def profile():
    return {'version': 1, 'command': ['/usr/bin/python3', '-I', '-m', 'example'],
            'environment': {'HERMES_HOME': '/var/lib/datacore-workers/test/.hermes'},
            'credential_names': ['EXAMPLE_API_KEY']}


def test_explicit_credentials_and_arguments_do_not_inherit_operator_environment(profile, monkeypatch):
    monkeypatch.setenv('SSH_AUTH_SOCK', '/operator/agent.socket')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'other-context-secret')
    monkeypatch.setenv('PYTHONPATH', '/operator/override')
    monkeypatch.setenv('DATACORE_ROOT', '/operator/Data')
    command, env = runtime.build_launch('test', profile, {'EXAMPLE_API_KEY': 'assigned-only'})
    assert command == profile['command'] and command is not profile['command']
    assert env['EXAMPLE_API_KEY'] == 'assigned-only'
    assert env['DATACORE_ROOT'] == '/var/lib/datacore-workers/test/Data'
    assert env['HOME'] == '/var/lib/datacore-workers/test'
    assert not {'SSH_AUTH_SOCK', 'AWS_SECRET_ACCESS_KEY', 'PYTHONPATH'} & set(env)
    assert 'other-context-secret' not in env.values()
    assert 'assigned-only' not in command


@pytest.mark.parametrize('name', ['../test', 'test/other', 'test\nUser=root', '%i', '', 'root space', '-test', 'a'*25])
def test_runtime_name_cannot_select_another_configuration(profile, name):
    with pytest.raises(runtime.Refused):
        runtime.build_launch(name, profile, {'EXAMPLE_API_KEY': 'assigned'})


@pytest.mark.parametrize('key', ['HOME', 'PATH', 'DATACORE_ROOT', 'DATACORE_STATE',
    'SSH_AUTH_SOCK', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONSTARTUP', 'NODE_OPTIONS',
    'BASH_ENV', 'ENV', 'DYLD_INSERT_LIBRARIES', 'CREDENTIALS_DIRECTORY'])
@pytest.mark.parametrize('source', ['environment', 'credentials'])
def test_loader_and_identity_overrides_are_refused(profile, key, source):
    secrets = {'EXAMPLE_API_KEY': 'assigned'}
    if source == 'environment':
        profile['environment'][key] = 'override'
    else:
        profile['credential_names'].append(key)
        secrets[key] = 'override'
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, secrets)


@pytest.mark.parametrize('secrets', [{}, {'EXAMPLE_API_KEY': 'x', 'OTHER_API_KEY': 'y'},
    {'EXAMPLE_API_KEY': None}, {'EXAMPLE_API_KEY': {'nested': 'x'}}, {'EXAMPLE_API_KEY': 'nul\0value'}])
def test_missing_extra_or_malformed_credentials_do_not_start_a_provider(profile, secrets):
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, secrets)


@pytest.mark.parametrize('command', [[], ['python3'], ['/home/operator/bin/python'],
    ['/opt/datacore/../operator/python'], ['/usr/bin/python3', None], ['/usr/bin/python3', 'bad\0argument']])
def test_command_validation(profile, command):
    profile['command'] = command
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, {'EXAMPLE_API_KEY': 'x'})


def test_profile_cannot_silently_change_secret_scope(profile):
    profile['environment']['EXAMPLE_API_KEY'] = 'wrong'
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, {'EXAMPLE_API_KEY': 'assigned'})
    del profile['environment']['EXAMPLE_API_KEY']
    profile['credential_names'] *= 2
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, {'EXAMPLE_API_KEY': 'assigned'})


def test_false_version_and_unknown_profile_settings_refuse(profile):
    profile['version'] = True
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, {'EXAMPLE_API_KEY': 'assigned'})
    profile['version'] = 1
    profile['inherit_environment'] = True
    with pytest.raises(runtime.Refused):
        runtime.build_launch('test', profile, {'EXAMPLE_API_KEY': 'assigned'})


@pytest.mark.parametrize('contents', ['{"TOKEN":"first","TOKEN":"second"}', '[]',
    '{"nested":{"TOKEN":"first","TOKEN":"second"}}', '{"TOKEN":'])
def test_json_ambiguity_and_malformed_input_refused(tmp_path, contents):
    path = tmp_path/'secrets.json'
    path.write_text(contents)
    with pytest.raises(ValueError):
        runtime._json_file(path, root_owned=False)


def test_json_reader_refuses_links_fifos_and_excessive_input(tmp_path):
    real = tmp_path/'real.json'
    real.write_text('{}')
    link = tmp_path/'link.json'
    link.symlink_to(real)
    fifo = tmp_path/'fifo.json'
    os.mkfifo(fifo)
    large = tmp_path/'large.json'
    large.write_text(json.dumps({'TOKEN': 'x'*runtime.MAX_CONFIG_BYTES}))
    for path in [link, fifo, large]:
        with pytest.raises((OSError, runtime.Refused)):
            runtime._json_file(path, root_owned=False)


def test_operator_owned_configuration_is_not_an_authority(tmp_path):
    path = tmp_path/'profile.json'
    path.write_text('{}')
    with pytest.raises(runtime.Refused):
        runtime._json_file(path, root_owned=True)


@pytest.mark.parametrize('uid', [0, 1000, 1001, 65534])
def test_direct_operator_invocation_fails_before_reading_credentials(uid, monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: uid)
    monkeypatch.setattr(os, 'geteuid', lambda: uid)
    monkeypatch.setattr(runtime, '_json_file', lambda *a, **k: pytest.fail('read credentials before refusing identity'))
    with pytest.raises(runtime.Refused):
        runtime.launch('test')


@pytest.mark.parametrize('identity', ['datacore-runtime', 'dc-other', 'dc-test-extra'])
def test_shared_or_other_dynamic_identity_refused_before_credentials(identity, monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: 62001)
    monkeypatch.setattr(os, 'geteuid', lambda: 62001)
    monkeypatch.setattr(runtime.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_name=identity))
    monkeypatch.setattr(runtime, '_json_file', lambda *a, **k: pytest.fail('read credentials before refusing shared identity'))
    with pytest.raises(runtime.Refused, match='identity does not match'):
        runtime.launch('test')


def test_unresolvable_dynamic_identity_refused_before_credentials(monkeypatch):
    monkeypatch.setattr(os, 'getuid', lambda: 62001)
    monkeypatch.setattr(os, 'geteuid', lambda: 62001)
    def missing(uid):
        raise KeyError(uid)
    monkeypatch.setattr(runtime.pwd, 'getpwuid', missing)
    monkeypatch.setattr(runtime, '_json_file', lambda *a, **k: pytest.fail('read credentials before refusing unresolved identity'))
    with pytest.raises(runtime.Refused, match='cannot be resolved'):
        runtime.launch('test')


def test_launch_ignores_forged_credentials_directory(profile, tmp_path, monkeypatch):
    reads = []
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', '/operator/another-service')
    monkeypatch.setattr(runtime, 'check_boundary', lambda name: None)
    def read(path, *, root_owned):
        reads.append((str(path), root_owned))
        return profile if root_owned else {'EXAMPLE_API_KEY': 'assigned'}
    monkeypatch.setattr(runtime, '_json_file', read)
    captured = {}
    monkeypatch.setattr(os, 'execve', lambda path, command, env: captured.update(command=command, env=env))
    monkeypatch.setattr(os, 'umask', lambda value: captured.update(umask=value))
    runtime.launch('test')
    assert reads == [('/etc/datacore/runtime/test.json', True),
        ('/run/credentials/datacore-runtime@test.service/secrets.json', False)]
    assert captured['umask'] == 0o077
    assert 'CREDENTIALS_DIRECTORY' not in captured['env']


def test_startup_errors_cannot_print_credential_values(monkeypatch, capsys):
    def invalid(name):
        raise ValueError('sensitive-credential-content')
    monkeypatch.setattr(runtime, 'launch', invalid)
    assert runtime.main(['launch', 'test']) == 1
    output = capsys.readouterr()
    assert 'sensitive-credential-content' not in output.err + output.out
    assert 'refused' in output.err
