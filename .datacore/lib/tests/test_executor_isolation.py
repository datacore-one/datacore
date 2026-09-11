"""Concurrent callers cannot cross-contaminate executor scope or accounting."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from executors.base import Executor
from ledger.log import read_events


class DelayedExecutor(Executor):
    name = 'delayed-test'

    def _invoke(self, prompt, timeout_s):
        self._model = prompt
        self._cost_estimated = prompt == 'first'
        self._in_band_error = 'first error' if prompt == 'first' else None
        time.sleep(0.05)
        return str(self._cwd), 1


def test_shared_executor_retains_each_runs_scope_and_accounting(tmp_path, monkeypatch):
    monkeypatch.delenv('DATACORE_NO_SPEND', raising=False)
    monkeypatch.setenv('DATACORE_LEDGER_SIGN', '0')
    monkeypatch.setenv('DATACORE_ACTOR', 'ambient-actor')
    ex = DelayedExecutor()
    start = threading.Barrier(2)

    def run(name):
        start.wait()
        return ex.run(name, cwd=tmp_path / name, space=tmp_path / name, item=name, actor=name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(run, ['first', 'second']))
    assert first.text == str(tmp_path / 'first')
    assert second.text == str(tmp_path / 'second')
    assert (first.model, second.model) == ('first', 'second')
    assert (first.error, second.error) == ('first error', None)
    for name in ('first', 'second'):
        events = read_events(tmp_path / name)
        assert len(events) == 1
        assert events[0].actor == name
        assert events[0].payload['item'] == name
        assert events[0].payload['model'] == name
        assert (':est' in events[0].payload['ref']) == (name == 'first')


def test_claude_binds_guard_and_keeps_prompt_out_of_process_arguments(tmp_path, monkeypatch):
    import json
    import subprocess
    import executors.claude_code as module
    monkeypatch.setenv('DATACORE_NO_SPEND', '1')
    monkeypatch.setenv('DATACORE_ACTOR', 'worker')
    monkeypatch.setenv('DATACORE_POLICY_GRANTED', 'payment')
    monkeypatch.setenv('DATACORE_POLICY_PRINCIPAL', 'forged')
    monkeypatch.setattr(module.shutil, 'which', lambda name: '/fake/claude')
    captured = {}
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps({'result': 'ok'}), '')
    monkeypatch.setattr(module, 'run_process', run)
    result = module.ClaudeCodeExecutor().run('private task content', cwd=tmp_path, actor='worker')
    assert result.error is None
    assert 'private task content' not in captured['command']
    assert captured['input'] == 'private task content'
    settings = json.loads(captured['command'][captured['command'].index('--settings') + 1])
    assert settings['hooks']['PreToolUse'][0]['matcher'] == '*'
    assert captured['env']['DATACORE_POLICY_GRANTED'] == ''
    assert captured['env']['DATACORE_POLICY_PRINCIPAL'] == 'worker'


def test_openclaw_uses_isolated_workspace_and_preserves_unicode_output(tmp_path, monkeypatch):
    import json
    import subprocess
    import executors.openclaw as module
    monkeypatch.setenv('DATACORE_NO_SPEND', '1')
    monkeypatch.setenv('DATACORE_ACTOR', 'worker')
    monkeypatch.setattr(module.shutil, 'which', lambda name: '/fake/openclaw')
    captured = {}
    body = 'result with │ diagram\n◇ data'
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps({'ok': True, 'status': 'ok', 'final': body, 'costUsd': 0.02, 'model': 'observed'}), '')
    monkeypatch.setattr(module, 'run_process', run)
    result = module.OpenClawExecutor().run('private content', cwd=tmp_path)
    assert result.error is None and result.text == body
    assert result.model == 'observed' and result.cost_cents == 2
    assert captured['command'][1:3] == ['agent', 'exec']
    assert captured['command'][captured['command'].index('--cwd') + 1] == str(tmp_path)
    assert '--state-dir' not in captured['command'] and '--agent' not in captured['command']
    assert captured['input'] == 'private content'


def test_hermes_dispatch_policy_errors_refuse_execution(monkeypatch):
    import types
    import executors.hermes_oneshot as wrapper
    import hermes_plugin
    plugins = types.SimpleNamespace(get_pre_tool_call_block_message=lambda *a, **kw: None)
    monkeypatch.setitem(__import__('sys').modules, 'hermes_cli', types.SimpleNamespace(plugins=plugins))
    monkeypatch.setattr(hermes_plugin, 'pre_tool_call', lambda **kw: {'action': 'block', 'message': 'denied'})
    wrapper.install_policy_guard()
    assert plugins.get_pre_tool_call_block_message('terminal', {}) == 'denied'
    def broken(**kwargs):
        raise RuntimeError('broken guard')
    monkeypatch.setattr(hermes_plugin, 'pre_tool_call', broken)
    wrapper.install_policy_guard()
    assert 'refused' in plugins.get_pre_tool_call_block_message('terminal', {})
