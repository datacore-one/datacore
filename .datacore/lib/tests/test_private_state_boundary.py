"""Private diagnostic state cannot alias data, code or shared directories."""
import pytest

from file_utils import private_state_directory
from hook_state import state_path
import workflow_executor
import org_transaction


@pytest.mark.parametrize('consumer', ['helper', 'hook', 'workflow', 'org-transaction'])
@pytest.mark.parametrize('kind', ['empty', 'relative', 'symlink', 'ancestor-symlink', 'data',
                                 'git-directory', 'git-file', 'public', 'writable-parent'])
def test_all_private_state_consumers_refuse_unsafe_roots(tmp_path, monkeypatch, consumer, kind):
    root = tmp_path / 'Data'
    root.mkdir()
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    target = tmp_path / 'state'
    if kind == 'empty':
        value = ''
    elif kind == 'relative':
        value = 'relative-state'
    else:
        if kind in ('symlink', 'ancestor-symlink'):
            real = tmp_path / 'retained'
            real.mkdir(mode=0o700)
            target.symlink_to(real, target_is_directory=True)
            if kind == 'ancestor-symlink':
                target /= 'nested'
        elif kind == 'data':
            target = root / 'private-state'
        elif kind in ('git-directory', 'git-file'):
            target.mkdir(mode=0o700)
            marker = target / '.git'
            if kind == 'git-directory':
                marker.mkdir()
            else:
                marker.write_text('gitdir: /synthetic/linked-worktree\n')
        elif kind == 'public':
            target.mkdir(mode=0o755)
            target.chmod(0o755)
        elif kind == 'writable-parent':
            target.mkdir(mode=0o777)
            target.chmod(0o777)
            target /= 'private-child'
        value = str(target)
    monkeypatch.setenv('DATACORE_STATE', value)
    def invoke():
        if consumer == 'helper':
            return private_state_directory('fixture')
        if consumer == 'hook':
            return state_path('fixture')
        if consumer == 'org-transaction':
            return org_transaction.journal_path()
        return workflow_executor._state_file()
    with pytest.raises((ValueError, OSError, workflow_executor.WorkflowError)):
        invoke()
    assert not (root / 'private-state').exists()


def test_new_parent_directories_are_private_and_default_is_outside_data(tmp_path, monkeypatch):
    monkeypatch.delenv('DATACORE_STATE', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))
    target = private_state_directory('reports/fixture')
    assert target == tmp_path / '.datacore/state/reports/fixture'
    for path in [tmp_path / '.datacore', tmp_path / '.datacore/state', target.parent, target]:
        assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('namespace', ['../outside', '/absolute', 'a//b', 'a/../b', 'a\\b'])
def test_namespace_cannot_escape_private_state(tmp_path, monkeypatch, namespace):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    with pytest.raises(ValueError):
        private_state_directory(namespace)


def test_filesystem_root_is_never_private_state(monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', '/')
    with pytest.raises(ValueError):
        private_state_directory()


def test_transaction_state_and_hook_state_share_private_initialization(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    journal = org_transaction.journal_path()
    assert journal.parent.stat().st_mode & 0o077 == 0
    assert state_path('fixture').parent.parent == journal.parent
