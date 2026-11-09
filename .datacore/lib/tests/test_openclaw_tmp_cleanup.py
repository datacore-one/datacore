"""An OpenClaw run leaves nothing in the host's temp directory.

`openclaw agent exec` creates a fresh ~108 MB state directory
(/tmp/openclaw-agent-exec-*) on every run and never removes it. A claim retried
every 15 minutes through it filled plur-claw's disk between 2026-09-28 and
2026-09-30. The executor therefore gives each run a private TMPDIR and removes
it afterwards, whatever the outcome.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_executor_isolation import declared_executor  # noqa: F401  (fixture)


def _leaky_run(seen, envelope, rc=0):
    def run(command, **kwargs):
        tmp = Path(kwargs['env']['TMPDIR'])
        seen.append(tmp)
        leak = tmp / 'openclaw-agent-exec-Ab12Cd' / 'agents/main/agent/codex-home'
        leak.mkdir(parents=True)
        (leak / 'blob').write_bytes(b'x' * 1024)
        return subprocess.CompletedProcess(command, rc, json.dumps(envelope), '')
    return run


@pytest.mark.parametrize('name', ['OpenClawExecutor', 'OpenClawGatewayExecutor'])
@pytest.mark.parametrize('outcome', ['ok', 'failed'])
def test_openclaw_run_leaves_no_temp_state(tmp_path, monkeypatch, declared_executor, name, outcome):  # noqa: F811
    import executors.openclaw as module
    host_tmp = tmp_path / 'host-tmp'; host_tmp.mkdir()
    monkeypatch.setenv('TMPDIR', str(host_tmp))
    monkeypatch.setattr(module.tempfile, 'tempdir', None)
    monkeypatch.setenv('DATACORE_NO_SPEND', '1')
    monkeypatch.setenv('DATACORE_ACTOR', 'worker')
    monkeypatch.setattr(module.shutil, 'which', lambda n: '/fake/openclaw')
    if name == 'OpenClawExecutor':
        envelope = ({'ok': True, 'status': 'ok', 'final': 'done'} if outcome == 'ok'
                    else {'ok': False, 'status': 'error', 'final': '', 'error': 'You have no credits remaining'})
    else:
        envelope = ({'status': 'ok', 'result': {'payloads': [{'text': 'done'}]}} if outcome == 'ok'
                    else {'status': 'error', 'error': 'turn failed'})
    seen = []
    monkeypatch.setattr(module, 'run_process', _leaky_run(seen, envelope, 0 if outcome == 'ok' else 2))
    getattr(module, name)().run('task', cwd=tmp_path)
    assert seen, 'openclaw was not started'
    assert seen[0].parent == host_tmp          # the private dir lives in the host temp dir ...
    assert not seen[0].exists()                # ... and is gone after the run
    assert list(host_tmp.iterdir()) == []      # nothing else left behind


def test_openclaw_temp_is_removed_even_when_the_run_raises(tmp_path, monkeypatch, declared_executor):  # noqa: F811
    import executors.openclaw as module
    host_tmp = tmp_path / 'host-tmp'; host_tmp.mkdir()
    monkeypatch.setenv('TMPDIR', str(host_tmp))
    monkeypatch.setattr(module.tempfile, 'tempdir', None)
    monkeypatch.setenv('DATACORE_NO_SPEND', '1')
    monkeypatch.setenv('DATACORE_ACTOR', 'worker')
    monkeypatch.setattr(module.shutil, 'which', lambda n: '/fake/openclaw')

    def boom(command, **kwargs):
        Path(kwargs['env']['TMPDIR'], 'openclaw-agent-exec-Zz').mkdir()
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(module, 'run_process', boom)
    module.OpenClawExecutor().run('task', cwd=tmp_path)
    assert list(host_tmp.iterdir()) == []
