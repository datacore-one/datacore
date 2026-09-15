"""Behavioral regression tests for hook filesystem, input, and CLI boundaries."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

import pytest

LIB = Path(__file__).resolve().parents[1]


def load_hook():
    spec = importlib.util.spec_from_file_location("inject_boundary", LIB / "hooks/plur_inject_wrapper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook(tmp_path, monkeypatch):
    module = load_hook()
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    return module


def test_predictable_legacy_symlink_cannot_truncate_data(hook, tmp_path):
    victim = tmp_path / "valid-data"
    victim.write_text("preserve every byte")
    (tmp_path / "plur-inject-example.lock").symlink_to(victim)
    lock = hook._try_lock("example")
    if lock is not None:
        lock.close()
    assert victim.read_text() == "preserve every byte"


@pytest.mark.parametrize("payload", ["null", "[]", "42", "false", '{"session_id": []}'])
def test_malformed_input_is_inert(hook, monkeypatch, capsys, payload):
    calls = []
    monkeypatch.setattr(hook, "_run_hook", lambda *a, **k: calls.append(a) or "{}")
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    hook.main([])
    assert json.loads(capsys.readouterr().out) == {}
    assert calls == []


def test_missing_cli_never_uses_a_package_runner(hook, tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_SHIM", tmp_path / "missing", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("DATACORE_PLUR_CLI", raising=False)
    with pytest.raises((FileNotFoundError, ValueError)):
        hook._hook_cmd()


def test_explicit_cli_is_exclusive(hook, tmp_path, monkeypatch):
    executable = tmp_path / "qualified-cli"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    monkeypatch.setenv("DATACORE_PLUR_CLI", str(executable))
    assert hook._hook_cmd() == [str(executable), "hook-inject"]


def test_malformed_dependency_response_is_inert(hook, monkeypatch, capsys):
    monkeypatch.setattr(hook, "_run_hook", lambda *a, **k: "[]")
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"example"}'))
    hook.main([])
    assert isinstance(json.loads(capsys.readouterr().out), dict)


@pytest.mark.parametrize('identity', ['a/b', 'a_b', '../escape', '', 'é', '\x00'])
def test_lock_is_private_and_serializes_same_session(hook, identity):
    import stat
    from hook_state import state_path
    first = hook._try_lock(identity)
    assert first is not None
    path = state_path('plur-inject', identity)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert hook._try_lock(identity) is None
    first.close()
    retry = hook._try_lock(identity)
    assert retry is not None
    retry.close()


def test_different_session_keys_do_not_collide(hook):
    first = hook._try_lock('a/b')
    second = hook._try_lock('a_b')
    assert first is not None and second is not None
    first.close()
    second.close()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'public'])
def test_hostile_private_lock_inode_is_refused_without_modifying_data(hook, tmp_path, kind):
    from hook_state import state_path
    target = tmp_path / 'target'
    target.write_text('important data')
    target.chmod(0o600)
    path = state_path('plur-inject', 'example')
    if kind == 'symlink':
        path.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, path)
    elif kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        path.write_text('existing state')
        path.chmod(0o644)
    assert hook._try_lock('example') is None
    assert target.read_text() == 'important data'


@pytest.mark.parametrize('payload', ['{"session_id":"' + 'x' * 4097 + '"}',
                                    '{"prompt":"' + 'x' * (512 * 1024) + '"}',
                                    '[' * 2000 + '0' + ']' * 2000])
def test_excessive_or_deep_input_never_launches_a_child(hook, monkeypatch, capsys, payload):
    monkeypatch.setattr(hook, '_run_hook', lambda *a, **k: pytest.fail('child launched'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(payload))
    hook.main([])
    assert json.loads(capsys.readouterr().out) == {}


def test_rehydrate_preserves_payload_and_flag(hook, monkeypatch, capsys):
    seen = []
    raw = json.dumps({'session_id': 's', 'prompt': 'valid unusual \u2603 input'})
    monkeypatch.setattr(sys, 'stdin', io.StringIO(raw))
    monkeypatch.setenv('DATACORE_HEADLESS', '1')
    monkeypatch.setattr(hook, '_run_hook', lambda data, **kw: seen.append((data, kw)) or '{"additionalContext":"memory"}')
    hook.main(['--rehydrate'])
    assert seen == [(raw, {'rehydrate': True})]
    assert json.loads(capsys.readouterr().out) == {'additionalContext': 'memory'}


def test_untrusted_sentinel_is_only_an_advisory_signal(hook, tmp_path):
    marker = tmp_path / 'plur-session-example'
    marker.write_text('')
    marker.chmod(0o600)
    assert hook._session_marked('example')
    marker.chmod(0o666)
    assert not hook._session_marked('example')
    marker.unlink()
    marker.symlink_to(tmp_path)
    assert not hook._session_marked('example')
    assert not hook._session_marked('../example')
    assert not hook._session_marked('example' * 100)


def test_exited_leader_cannot_leave_child_writing_after_timeout(hook, tmp_path, monkeypatch):
    import time
    marker = tmp_path / 'late-write'
    ready = tmp_path / 'ready'
    child = ('import pathlib,time; pathlib.Path(' + repr(str(ready)) + ').touch(); '
             'time.sleep(1); pathlib.Path(' + repr(str(marker)) + ').write_text("late")')
    leader = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",' + repr(child) + '])'
    monkeypatch.setattr(hook, '_hook_cmd', lambda: [sys.executable, '-c', leader])
    assert hook._run_hook('{}', timeout=0.5) == ''
    assert ready.exists()
    time.sleep(0.8)
    assert not marker.exists()
