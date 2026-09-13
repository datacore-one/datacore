import fcntl
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

import workflow_executor as workflow
import file_utils


@pytest.fixture
def state(tmp_path, monkeypatch):
    private = tmp_path / 'private-state'
    private.mkdir(mode=0o700)
    code = tmp_path / 'code/.datacore/lib/workflow_executor.py'
    code.parent.mkdir(parents=True)
    legacy = code.parent.parent / 'state'
    monkeypatch.setattr(workflow, '__file__', str(code))
    monkeypatch.setattr(workflow, 'STATE_DIR', legacy, raising=False)
    monkeypatch.setattr(workflow, 'STATE_FILE', legacy / 'workflow_state.yaml', raising=False)
    monkeypatch.setenv('DATACORE_STATE', str(private))
    monkeypatch.setenv('DATACORE_ROOT', str(tmp_path / 'data'))
    return private / 'workflow_state.yaml', legacy / 'workflow_state.yaml'


def test_state_is_bound_to_declared_runtime_not_source_code(state):
    target, legacy = state
    workflow._update_phase_state('flow', 'phase', 'completed')
    assert target.is_file()
    assert not legacy.exists()


@pytest.mark.parametrize('data', ['workflows: [\n', 'null\n', 'workflows: {first: {phases: {}}}\nworkflows: {}\n'])
def test_malformed_state_cannot_be_reset_and_overwritten(state, monkeypatch, data):
    target, _ = state
    target.write_text(data)
    monkeypatch.setattr(workflow, 'STATE_DIR', target.parent)
    monkeypatch.setattr(workflow, 'STATE_FILE', target)
    with pytest.raises((ValueError, workflow.WorkflowError)):
        workflow._update_phase_state('new', 'phase', 'completed')
    assert target.read_text() == data


def test_existing_unmigrated_state_is_not_hidden_by_an_empty_new_store(state):
    target, legacy = state
    legacy.parent.mkdir()
    legacy.write_text('workflows: {existing: {phases: {}}}\n')
    with pytest.raises((ValueError, workflow.WorkflowError)):
        workflow._update_phase_state('new', 'phase', 'completed')
    assert not target.exists()
    assert 'existing' in legacy.read_text()


def test_actual_lock_holder_prevents_an_uncoordinated_state_write(state):
    target, _ = state
    target.write_text('workflows: {existing: {phases: {}}}\n')
    original = target.read_bytes()
    lock = target.parent / '.workflow_state.yaml.lock'
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        script = ('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                  'import workflow_executor as w;w.STATE_DIR=Path(sys.argv[2]);'
                  'w.STATE_FILE=w.STATE_DIR/"workflow_state.yaml";'
                  'w._update_phase_state("new","phase","completed")')
        result = subprocess.run([sys.executable, '-I', '-c', script, str(Path(__file__).parents[1]), str(target.parent)],
                                capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, DATACORE_STATE=str(target.parent)))
    assert result.returncode != 0
    assert target.read_bytes() == original


def test_normal_update_preserves_other_workflows_phases_and_metadata(state, monkeypatch):
    target, _ = state
    original = {'note': 'retained', 'workflows': {'other': {'phases': {'one': {'status': 'completed'}}}}}
    target.write_text(yaml.safe_dump(original))
    monkeypatch.setattr(workflow, 'STATE_FILE', target)
    monkeypatch.setattr(workflow, 'STATE_DIR', target.parent)
    workflow._update_phase_state('new', 'second', 'completed', 'done')
    value = yaml.safe_load(target.read_text())
    assert value['note'] == 'retained'
    assert value['workflows']['other'] == original['workflows']['other']
    assert value['workflows']['new']['phases']['second']['status'] == 'completed'


def test_short_reads_preserve_complete_state(state, monkeypatch):
    target, _ = state
    original = {'note': '€🙂' * 100, 'workflows': {}}
    target.write_text(yaml.safe_dump(original))
    real_read = os.read
    monkeypatch.setattr(workflow.os, 'read', lambda fd, size: real_read(fd, min(size, 7)))
    workflow._update_phase_state('new', 'phase', 'completed')
    assert yaml.safe_load(target.read_text())['note'] == original['note']


def test_failed_publication_preserves_previous_state(state, monkeypatch):
    target, _ = state
    original = b'workflows: {existing: {phases: {}}}\n'
    target.write_bytes(original)
    def fail(*args):
        raise OSError('injected storage failure')
    monkeypatch.setattr(file_utils.os, 'fsync', fail)
    with pytest.raises(OSError, match='storage failure'):
        workflow._update_phase_state('new', 'phase', 'completed')
    assert target.read_bytes() == original


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'oversize', 'utf8'])
def test_unsafe_state_is_refused_without_replacement(state, kind):
    target, _ = state
    other = target.parent / 'retained'
    other.write_bytes(b'workflows: {}\n')
    if kind == 'symlink':
        target.symlink_to(other)
    elif kind == 'hardlink':
        os.link(other, target)
    elif kind == 'fifo':
        os.mkfifo(target)
    else:
        target.write_bytes(b'\xff' if kind == 'utf8' else b'x' * (4 * 1024**2 + 1))
    inode = target.lstat().st_ino
    with pytest.raises((OSError, ValueError, workflow.WorkflowError)):
        workflow._update_phase_state('new', 'phase', 'completed')
    assert target.lstat().st_ino == inode
    assert other.read_bytes() == b'workflows: {}\n'


def test_simultaneous_workflows_preserve_all_completions(state):
    target, _ = state
    script = ('import sys;sys.path.insert(0,sys.argv[1]);import workflow_executor as w;'
              'w._update_phase_state(sys.argv[2],"phase","completed")')
    children = [subprocess.Popen([sys.executable, '-I', '-c', script,
                                 str(Path(__file__).parents[1]), f'workflow-{n}'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, DATACORE_STATE=str(target.parent)))
                for n in range(8)]
    for child in children:
        _, error = child.communicate(timeout=15)
        assert child.returncode == 0, error
    assert set(yaml.safe_load(target.read_text())['workflows']) == {f'workflow-{n}' for n in range(8)}
