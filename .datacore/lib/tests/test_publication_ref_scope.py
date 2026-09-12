"""A publication must not inherit unrelated refs or later local commits."""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import knowledge_commit
import ledger_publish_safe
from git_publication import push_arguments


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def fixture(tmp_path):
    repo, origin = tmp_path / 'repo', tmp_path / 'origin.git'
    repo.mkdir()
    git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(origin))
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.test')
    git(repo, 'remote', 'add', 'origin', str(origin))
    (repo / 'seed').write_text('seed\n')
    git(repo, 'add', '--', 'seed')
    git(repo, 'commit', '-qm', 'seed')
    git(repo, 'push', '-qu', 'origin', 'main')
    return repo, origin


def test_off_branch_publication_cannot_follow_private_annotated_tags(tmp_path):
    repo, origin = fixture(tmp_path)
    git(repo, 'tag', '-am', 'private tag metadata', 'private-tag')
    git(repo, 'config', 'push.followTags', 'true')
    git(repo, 'checkout', '-qb', 'feature')
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Allowed note.\n')
    sha = knowledge_commit.commit_to_branch(repo, 'main', ['journal/note.md'], 'publish note')
    assert git(origin, 'rev-parse', 'main') == sha
    assert git(origin, 'tag', '--list') == ''


def test_same_branch_push_uses_captured_commit_not_later_head(tmp_path):
    repo, origin = fixture(tmp_path)
    captured = git(repo, 'rev-parse', 'HEAD')
    (repo / 'later-private.txt').write_text('Not part of captured publication.\n')
    git(repo, 'add', '--', 'later-private.txt')
    git(repo, 'commit', '-qm', 'later independent work')
    knowledge_commit._push_converging(repo, 'main', captured)
    assert git(origin, 'rev-parse', 'main') == captured


def test_machine_publisher_cannot_push_an_unrelated_matching_branch(tmp_path):
    repo, origin = fixture(tmp_path)
    git(repo, 'checkout', '-qb', 'private-work')
    git(repo, 'push', '-q', 'origin', 'private-work')
    before = git(origin, 'rev-parse', 'private-work')
    (repo / 'private.txt').write_text('Unreviewed content.\n')
    git(repo, 'add', '--', 'private.txt')
    git(repo, 'commit', '-qm', 'unreviewed local work')
    git(repo, 'checkout', '-q', 'main')
    machine = repo / '.datacore/events/fixture.jsonl'
    machine.parent.mkdir(parents=True)
    machine.write_text('{"fixture": true}\n')
    git(repo, 'config', 'push.default', 'matching')
    status, detail = ledger_publish_safe.publish(repo, ['.datacore/events/fixture.jsonl'])
    assert status == 'ok', detail
    assert git(origin, 'rev-parse', 'private-work') == before


def test_machine_publisher_pins_the_history_it_actually_validated(tmp_path, monkeypatch):
    repo, origin = fixture(tmp_path)
    machine = repo / '.datacore/events/fixture.jsonl'
    machine.parent.mkdir(parents=True)
    machine.write_text('{"fixture": true}\n')
    validate = ledger_publish_safe._outgoing_is_machine_only
    captured = None
    def interleave(space, source=None, upstream=None):
        nonlocal captured
        result = validate(space, source, upstream)
        if source is not None:
            captured = source
            (repo / 'private-later.txt').write_text('Not reviewed.\n')
            git(repo, 'add', '--', 'private-later.txt')
            git(repo, 'commit', '-qm', 'later independent work')
        return result
    monkeypatch.setattr(ledger_publish_safe, '_outgoing_is_machine_only', interleave)
    assert ledger_publish_safe.publish(repo, ['.datacore/events/fixture.jsonl'])[0] == 'ok'
    assert captured and git(origin, 'rev-parse', 'main') == captured
    assert (repo / 'private-later.txt').exists()
    assert 'private-later.txt' not in git(origin, 'ls-tree', '--name-only', 'main')


def test_explicit_lease_rejects_a_new_remote_owner(tmp_path):
    repo, origin = fixture(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'candidate').write_text('candidate\n')
    git(repo, 'add', '--', 'candidate')
    git(repo, 'commit', '-qm', 'candidate')
    candidate = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-qb', 'other-owner', base)
    (repo / 'other').write_text('other owner\n')
    git(repo, 'add', '--', 'other')
    git(repo, 'commit', '-qm', 'other')
    other = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'push', '-q', 'origin', f'{other}:refs/heads/main')
    git(repo, 'fetch', '-q', 'origin')  # tracking-ref movement cannot weaken the lease
    result = subprocess.run(['git', '-C', str(repo),
                             *push_arguments(candidate, 'refs/heads/main', expected=base)],
                            capture_output=True)
    assert result.returncode != 0
    assert git(origin, 'rev-parse', 'main') == other


def test_retry_does_not_merge_or_publish_a_later_local_commit(tmp_path):
    repo, origin = fixture(tmp_path)
    captured = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-qb', 'remote-writer')
    (repo / 'remote-note').write_text('Remote work.\n')
    git(repo, 'add', '--', 'remote-note')
    git(repo, 'commit', '-qm', 'remote work')
    remote = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'push', '-q', 'origin', f'{remote}:refs/heads/main')
    git(repo, 'checkout', '-q', 'main')
    (repo / 'private-later').write_text('Later local work.\n')
    git(repo, 'add', '--', 'private-later')
    git(repo, 'commit', '-qm', 'later local work')
    local = git(repo, 'rev-parse', 'HEAD')
    index = (repo / '.git/index').read_bytes()
    with pytest.raises(knowledge_commit.GitError, match='source advanced'):
        knowledge_commit._push_converging(repo, 'main', captured)
    assert git(origin, 'rev-parse', 'main') == remote
    assert git(repo, 'rev-parse', 'HEAD') == local
    assert (repo / '.git/index').read_bytes() == index


def test_missing_submodule_commit_is_not_implicitly_published(tmp_path):
    repo, origin = fixture(tmp_path)
    child_root = tmp_path / 'child'
    child_root.mkdir()
    child, child_origin = fixture(child_root)
    old_child = git(child_origin, 'rev-parse', 'main')
    old_parent = git(origin, 'rev-parse', 'main')
    git(repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(child_origin), 'module')
    module = repo / 'module'
    git(module, 'config', 'user.name', 'Fixture')
    git(module, 'config', 'user.email', 'fixture@example.test')
    (module / 'unpublished').write_text('Requires a separate approved publication.\n')
    git(module, 'add', '--', 'unpublished')
    git(module, 'commit', '-qm', 'unpublished child')
    git(repo, 'add', '--', '.gitmodules', 'module')
    git(repo, 'commit', '-qm', 'parent reference')
    git(repo, 'config', 'push.recurseSubmodules', 'on-demand')
    result = subprocess.run(['git', '-C', str(repo),
                             *push_arguments(git(repo, 'rev-parse', 'HEAD'), 'refs/heads/main')],
                            capture_output=True)
    assert result.returncode != 0
    assert git(origin, 'rev-parse', 'main') == old_parent
    assert git(child_origin, 'rev-parse', 'main') == old_child


def test_append_only_mode_preserves_the_base_even_when_source_head_matches_it(tmp_path):
    repo, origin = fixture(tmp_path)
    log = repo / 'events.jsonl'
    complete = '{"seq":1}\n{"seq":2}\n'
    log.write_text(complete)
    git(repo, 'add', '--', 'events.jsonl')
    git(repo, 'commit', '-qm', 'complete log')
    git(repo, 'branch', 'candidate')
    before = git(repo, 'rev-parse', 'candidate')
    log.write_text('{"seq":1}\n')
    with pytest.raises(knowledge_commit.GitError, match='append-only'):
        knowledge_commit.commit_to_branch(repo, 'candidate', ['events.jsonl'], 'stale replacement',
                                          push=False, append_only=True)
    assert git(repo, 'rev-parse', 'candidate') == before
    assert git(repo, 'show', 'candidate:events.jsonl') == complete.strip()
    assert log.read_text() == '{"seq":1}\n'


@pytest.mark.parametrize('commit,destination,expected', [
    ('HEAD', 'refs/heads/main', None),
    ('0' * 40, 'refs/heads/main', None),
    ('a' * 40, 'refs/heads/*', None),
    ('a' * 40, 'refs/heads/main:refs/heads/other', None),
    ('a' * 40, 'refs/heads/main\nother', None),
    ('a' * 40, 'refs/tags/private', None),
    ('a' * 40, 'refs/heads/main', False),
    ('a' * 40, 'refs/heads/main', 'origin/main'),
])
def test_ambiguous_or_mutable_publication_identity_is_refused(commit, destination, expected):
    with pytest.raises(ValueError):
        push_arguments(commit, destination, expected=expected)
