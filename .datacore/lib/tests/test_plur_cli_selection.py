"""Installed CLI selection must not trigger package acquisition or leak inputs."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import plur_cli

LIB = Path(__file__).resolve().parents[1]


@pytest.fixture
def executable(tmp_path, monkeypatch):
    binary = tmp_path / 'qualified plur'
    binary.write_text('#!' + sys.executable + '\nimport json,sys\nprint(json.dumps({"directives":"memory", "count":1, "argv":sys.argv[1:]}))\n')
    binary.chmod(0o700)
    monkeypatch.setenv('DATACORE_PLUR_CLI', str(binary))
    return binary


def test_installed_cli_preserves_literal_arguments(executable):
    args = ('inject', 'a; $(unexpected) `command` \u2603', '--json')
    result = plur_cli.run(*args, capture_output=True, text=True, timeout=3)
    assert result.returncode == 0
    assert json.loads(result.stdout)['argv'] == list(args)


@pytest.mark.parametrize('override', ['', 'relative', '/missing/datacore-plur', '/tmp/../missing'])
def test_invalid_override_cannot_fall_back_to_path(tmp_path, monkeypatch, override):
    binary = tmp_path / 'plur'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setenv('DATACORE_PLUR_CLI', override)
    with pytest.raises((OSError, ValueError)):
        plur_cli.command('inject')


def test_path_resolution_uses_installed_bin_link(executable, tmp_path, monkeypatch):
    link = tmp_path / 'plur'
    link.symlink_to(executable)
    monkeypatch.delenv('DATACORE_PLUR_CLI')
    monkeypatch.setenv('PATH', str(tmp_path))
    assert plur_cli.command('--version') == [str(link), '--version']


def test_active_memory_and_agent_hooks_use_selected_cli(executable, monkeypatch, tmp_path):
    import active_memory
    import hooks
    monkeypatch.setattr(hooks, 'REGISTRY_PATH', tmp_path / 'absent-registry')
    monkeypatch.setattr(hooks, 'HOOK_STATE_PATH', tmp_path / 'absent-state')
    assert active_memory.plur_inject('synthetic task') == ('memory', 1)
    assert hooks.HookExecutor()._inject_engrams('synthetic-agent', 'synthetic task') == '## Applicable Engrams\n\nmemory'


def test_learning_uses_selected_cli_with_literal_fields(executable):
    import plur_auto_promote
    ok, result = plur_auto_promote.create_engram_via_mcp({'statement': 'literal; text', 'type': 'behavioral', 'scope': 'private'})
    assert ok
    args = json.loads(result)['argv']
    assert args[0] == 'learn'
    assert args[args.index('--statement') + 1] == 'literal; text'
    assert args[args.index('--scope') + 1] == 'private'


def test_cli_failure_diagnostics_do_not_copy_private_stderr(executable, monkeypatch, tmp_path):
    import active_memory
    import hooks
    executable.write_text('#!' + sys.executable + '\nimport sys\nprint("synthetic-private-secret", file=sys.stderr)\nsys.exit(7)\n')
    logs = []
    monkeypatch.setattr(active_memory, '_debug', logs.append)
    monkeypatch.setattr(hooks, 'REGISTRY_PATH', tmp_path / 'absent-registry')
    monkeypatch.setattr(hooks, 'HOOK_STATE_PATH', tmp_path / 'absent-state')
    executor = hooks.HookExecutor()
    monkeypatch.setattr(executor, '_log', logs.append)
    assert active_memory.plur_inject('synthetic-private-task') is None
    assert executor._inject_engrams('agent', 'synthetic-private-task') is None
    assert any('7' in line for line in logs)
    assert 'synthetic-private' not in '\n'.join(logs)


def load_installer():
    spec = importlib.util.spec_from_file_location('configure_hook_boundary', LIB / 'bootstrap/configure-hooks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_quotes_paths_and_retires_only_owned_package_runner(tmp_path):
    module = load_installer()
    root = str(tmp_path / 'data; literal $(x)')
    required = module.build_required_hooks(root)
    for entries in required.values():
        for entry in entries:
            for hook in entry['hooks']:
                args = shlex.split(hook['command'])
                assert args[0] == 'python3' and args[1].startswith(root + '/')
    user_hook = {'command': 'echo preserve-my-hook'}
    settings = {'permissions': {'allow': ['Read']}, 'hooks': {'PostCompact': [{'matcher': 'auto|manual', 'hooks': [
        {'command': 'npx @plur-ai/cli hook-inject --rehydrate'}, user_hook]}]}}
    merged, _, _ = module.merge_hooks(settings, required)
    flattened = [h for e in merged['hooks']['PostCompact'] for h in e['hooks']]
    assert user_hook in flattened
    assert not any(h['command'].startswith('npx') for h in flattened)
    assert merged['permissions'] == {'allow': ['Read']}
    same, added, upgraded = module.merge_hooks(merged, required)
    assert same == merged and not added and not upgraded


def test_installer_failed_publication_preserves_all_settings(tmp_path, monkeypatch):
    import file_utils
    module = load_installer()
    root = tmp_path / 'installation'
    scripts = root / '.datacore/lib/hooks'
    scripts.mkdir(parents=True)
    for name in ('plur_inject_wrapper.py', 'command_recall_inject.py', 'plur_observe.py'):
        (scripts / name).write_text('')
    settings = tmp_path / 'settings.json'
    original = '{"permissions":{"allow":["Read"]},"custom":"preserve"}\n'
    settings.write_text(original)
    monkeypatch.setattr(module, 'SETTINGS_PATH', settings)
    monkeypatch.setattr(module, 'detect_datacore_root', lambda: str(root))
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(file_utils.os, 'replace', fail)
    with pytest.raises(OSError, match='synthetic disk failure'):
        module.main()
    assert settings.read_text() == original


def test_shipped_hooks_use_installed_cli_with_timeout_headroom():
    settings = json.loads((LIB.parent / 'settings.json').read_text())
    for entries in settings['hooks'].values():
        for entry in entries:
            for hook in entry['hooks']:
                command = hook['command']
                assert 'npx @plur-ai/cli' not in command
                if 'plur_cli.py' in command:
                    args = shlex.split(command)
                    assert float(args[args.index('--timeout') + 1]) < hook['timeout']


def test_installer_upgrades_owned_config_without_deleting_neighbor_hook():
    module = load_installer()
    required = module.build_required_hooks('/synthetic/install')
    command = required['UserPromptSubmit'][0]['hooks'][0]['command']
    neighbor = {'command': 'echo preserve-neighbor', 'timeout': 2}
    settings = {'hooks': {'UserPromptSubmit': [{'hooks': [
        {'command': command, 'type': 'command', 'timeout': 3}, neighbor]}]}}
    merged, _, upgraded = module.merge_hooks(settings, required)
    hooks = merged['hooks']['UserPromptSubmit'][0]['hooks']
    assert neighbor in hooks
    assert next(h for h in hooks if h['command'] == command)['timeout'] == 90
    assert upgraded


def test_harness_termination_stops_foreground_descendants(tmp_path, monkeypatch):
    import signal
    import time
    marker = tmp_path / 'late-write'
    ready = tmp_path / 'ready'
    child = ('import pathlib,time; pathlib.Path(' + repr(str(ready)) + ').touch(); '
             'time.sleep(2); pathlib.Path(' + repr(str(marker)) + ').write_text("late")')
    binary = tmp_path / 'plur'
    binary.write_text('#!' + sys.executable + '\nimport subprocess,sys,time\n'
                      'subprocess.Popen([sys.executable,"-c",' + repr(child) + '])\ntime.sleep(30)\n')
    binary.chmod(0o700)
    monkeypatch.setenv('DATACORE_PLUR_CLI', str(binary))
    wrapper = subprocess.Popen([sys.executable, str(LIB / 'plur_cli.py'), '--timeout', '20', 'inject'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        wrapper.send_signal(signal.SIGTERM)
        wrapper.communicate(timeout=3)
        assert wrapper.returncode == 128 + signal.SIGTERM
        time.sleep(2.1)
        assert not marker.exists()
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()


def test_installed_version_probe_cannot_report_registry_latest(tmp_path, monkeypatch):
    import update_check
    package = tmp_path / 'installed-package'
    (package / 'dist').mkdir(parents=True)
    binary = package / 'dist/cli.js'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o700)
    (package / 'package.json').write_text(json.dumps({'name': '@plur-ai/cli', 'version': '0.19.4'}))
    monkeypatch.setenv('DATACORE_PLUR_CLI', str(binary))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('metadata probe executed a process'))
    assert update_check._local_version_plur_cli() == '0.19.4'
    binary.unlink()
    assert update_check._local_version_plur_cli() is None


def test_hook_installer_has_no_undeclared_yaml_bootstrap_dependency(tmp_path):
    code = 'import runpy,sys; runpy.run_path(sys.argv[1],run_name="fixture")'
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
                             str(LIB / 'bootstrap/configure-hooks.py')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('version', [None, 12, [], {}, True])
def test_malformed_installed_version_is_unknown(version):
    import update_check
    assert update_check._validate_version(version) is None
