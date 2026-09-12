import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import commit_gate
import git_fleet_sync
import pytest


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=check).stdout


@pytest.fixture
def conflict(tmp_path, monkeypatch):
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '--bare', '-q', '-b', 'main', str(origin))
    repo = tmp_path / 'work'
    git(tmp_path, 'clone', '-q', str(origin), str(repo))
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.test')
    (repo / 'seed').write_text('seed\n')
    git(repo, 'add', '--', 'seed')
    git(repo, 'commit', '-qm', 'seed')
    git(repo, 'checkout', '-b', 'other')
    (repo / 'added').write_text('other version\n')
    git(repo, 'add', '--', 'added')
    git(repo, 'commit', '-qm', 'other')
    git(repo, 'checkout', 'main')
    (repo / 'added').write_text('main version\n')
    git(repo, 'add', '--', 'added')
    git(repo, 'commit', '-qm', 'main')
    git(repo, 'push', '-u', 'origin', 'main')
    git(repo, 'merge', 'other', check=False)
    assert git(repo, 'status', '--porcelain', '-z') == b'AA added\0'
    # Missing high-level operation metadata must not make an unresolved index
    # safe to stage. A linked worktree also stores MERGE_HEAD elsewhere.
    (repo / '.git' / 'MERGE_HEAD').unlink()
    monkeypatch.setattr(git_fleet_sync, 'review_gate', lambda *args: '')
    monkeypatch.setattr(commit_gate, 'PENDING', tmp_path / 'decisions')
    return repo, origin


def test_both_added_conflict_is_never_published(conflict):
    repo, origin = conflict
    before = git(origin, 'rev-parse', 'main')
    index = git(repo, 'ls-files', '--stage', '-z')
    contents = (repo / 'added').read_bytes()
    result = git_fleet_sync.sync_repo(repo, execute=True)
    assert not result['status'].startswith('PUSHED')
    assert git(origin, 'rev-parse', 'main') == before
    assert git(repo, 'ls-files', '--stage', '-z') == index
    assert (repo / 'added').read_bytes() == contents


def test_output_gate_cannot_authorize_unresolved_index(conflict):
    repo, _ = conflict
    with pytest.raises(RuntimeError):
        commit_gate.decide(repo, ['added'], task_id='fixture')


@pytest.mark.parametrize('status', ['DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'])
def test_every_unmerged_status_refuses_output_selection(conflict, monkeypatch, status):
    import git_inventory
    repo, _ = conflict
    monkeypatch.setattr(git_inventory, '_output', lambda *args: (status + ' added\0').encode())
    with pytest.raises(RuntimeError):
        git_inventory.dirty_paths(repo)


def test_knowledge_publisher_preserves_unmerged_index(conflict):
    import knowledge_commit
    repo, origin = conflict
    before = git(origin, 'rev-parse', 'main')
    index = git(repo, 'ls-files', '--stage', '-z')
    with pytest.raises(knowledge_commit.GitError):
        knowledge_commit.commit_to_branch(repo, 'main', ['added'], 'do not resolve implicitly')
    assert git(origin, 'rev-parse', 'main') == before
    assert git(repo, 'ls-files', '--stage', '-z') == index
