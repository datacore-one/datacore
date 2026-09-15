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


@pytest.mark.parametrize('off_branch', [False, True])
@pytest.mark.parametrize('changed', [False, True])
def test_selected_output_cannot_authorize_an_unpublished_parent(tmp_path, off_branch, changed):
    repo, origin = fixture(tmp_path)
    (repo / 'journal').mkdir()
    note = repo / 'journal/note.md'
    note.write_text('Previously acknowledged note.\n')
    git(repo, 'add', '--', 'journal/note.md')
    git(repo, 'commit', '-qm', 'acknowledged note')
    git(repo, 'push', '-q', 'origin', 'main')
    (repo / 'private.txt').write_text('Unrelated private work.\n')
    git(repo, 'add', '--', 'private.txt')
    git(repo, 'commit', '-qm', 'unpublished unrelated work')
    private = git(repo, 'rev-parse', 'HEAD')
    if off_branch:
        git(repo, 'checkout', '-qb', 'feature')
    if changed:
        note.write_text('Previously acknowledged note.\nOwned addition.\n')
    try:
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'owned note')
    except knowledge_commit.GitError:
        pass  # Refusal is safe; a private candidate is also acceptable.
    assert 'private.txt' not in git(origin, 'ls-tree', '-r', '--name-only', 'main').splitlines()
    assert git(repo, 'show', f'{private}:private.txt') == 'Unrelated private work.'
    assert (repo / 'private.txt').read_text() == 'Unrelated private work.\n'
    assert note.read_text().endswith('Owned addition.\n' if changed else 'acknowledged note.\n')


@pytest.mark.parametrize('off_branch', [False, True])
def test_verified_publication_chain_survives_rejected_push_and_restart(tmp_path, off_branch):
    repo, origin = fixture(tmp_path)
    if off_branch:
        git(repo, 'checkout', '-qb', 'feature')
    (repo / 'journal').mkdir()
    note = repo / 'journal/note.md'
    note.write_text('First owned output.\n')
    first = knowledge_commit.commit_to_branch(repo, 'main', ['journal/note.md'], 'first', push=False)
    note.write_text('First owned output.\nSecond owned output.\n')
    hook = origin / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    with pytest.raises(knowledge_commit.GitError):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'second')
    second = git(repo, 'rev-parse', 'main')
    assert git(repo, 'show', '-s', '--format=%P', second) == first
    hook.unlink()
    # A new interpreter must recover durable provenance, including a no-op
    # retry whose selected bytes were already committed before the push failed.
    result = subprocess.run([sys.executable, str(Path(knowledge_commit.__file__)),
                             str(repo), 'retry', 'journal/note.md'], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert git(origin, 'rev-parse', 'main') == second
    assert git(origin, 'show', 'main:journal/note.md') == note.read_text().strip()


def test_verified_tip_does_not_authorize_an_unverified_intermediate_commit(tmp_path):
    repo, origin = fixture(tmp_path)
    (repo / 'journal').mkdir()
    note = repo / 'journal/note.md'
    note.write_text('Owned first.\n')
    knowledge_commit.commit_to_branch(repo, 'main', ['journal/note.md'], 'first', push=False)
    (repo / 'private').write_text('Unrelated middle.\n')
    git(repo, 'add', '--', 'private')
    git(repo, 'commit', '-qm', 'unrelated middle')
    note.write_text('Owned first.\nOwned second.\n')
    with pytest.raises(knowledge_commit.GitError, match='unverified local history'):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'second')
    assert 'private' not in git(origin, 'ls-tree', '-r', '--name-only', 'main').splitlines()
    assert (repo / 'private').read_text() == 'Unrelated middle.\n'


@pytest.mark.parametrize('changed_binding', ['origin', 'branch'])
def test_local_receipts_cannot_authorize_a_different_destination(tmp_path, changed_binding):
    repo, origin = fixture(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    other = tmp_path / 'other.git'
    git(tmp_path, 'clone', '-q', '--bare', str(origin), str(other))
    git(repo, 'push', '-q', 'origin', 'main:other')
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Owned for main on the original remote.\n')
    sha = knowledge_commit.commit_to_branch(repo, 'main', ['journal/note.md'], 'owned', push=False)
    if changed_binding == 'origin':
        git(repo, 'remote', 'set-url', 'origin', str(other))
    with pytest.raises(knowledge_commit.GitError, match='unverified local history'):
        knowledge_commit._push_commit(repo, 'other' if changed_binding == 'branch' else 'main', sha)
    assert git(origin, 'rev-parse', 'main') == base
    assert git(origin, 'rev-parse', 'other') == base
    assert git(other, 'rev-parse', 'main') == base


def test_remote_rewind_during_publication_cannot_republish_removed_history(tmp_path, monkeypatch):
    repo, origin = fixture(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'previously-shared').write_text('Removed by an independent remote owner.\n')
    git(repo, 'add', '--', 'previously-shared')
    git(repo, 'commit', '-qm', 'old remote history')
    git(repo, 'push', '-q', 'origin', 'main')
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Current owned output.\n')
    original = knowledge_commit._git
    rewound = []
    def interleave(directory, *args, **kwargs):
        if args[0] == 'push' and not rewound:
            git(origin, 'update-ref', 'refs/heads/main', base)
            rewound.append(True)
        return original(directory, *args, **kwargs)
    monkeypatch.setattr(knowledge_commit, '_git', interleave)
    with pytest.raises(knowledge_commit.GitError):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'owned')
    assert rewound
    assert git(origin, 'rev-parse', 'main') == base
    assert (repo / 'previously-shared').exists()
    assert (repo / 'journal/note.md').read_text() == 'Current owned output.\n'


def test_pre_push_origin_change_cannot_redirect_verified_content(tmp_path):
    repo, origin = fixture(tmp_path)
    other = tmp_path / 'other.git'
    git(tmp_path, 'clone', '-q', '--bare', str(origin), str(other))
    base = git(other, 'rev-parse', 'main')
    hook = repo / '.git/hooks/pre-push'
    # The path is an argv value in the hook; no fixture content is interpolated
    # into shell code. The URL is stored in a separate operator config entry.
    git(repo, 'config', 'test.otherOrigin', str(other))
    hook.write_text('#!/bin/sh\ngit remote set-url origin "$(git config test.otherOrigin)"\n')
    hook.chmod(0o755)
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Owned for the original remote.\n')
    result = knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'owned')
    assert git(origin, 'rev-parse', 'main') == result['commit']
    assert git(other, 'rev-parse', 'main') == base


def test_shallow_history_cannot_hide_an_unverified_parent(tmp_path):
    original, origin = fixture(tmp_path)
    repo = tmp_path / 'shallow'
    git(tmp_path, 'clone', '-q', '--depth', '1', origin.as_uri(), str(repo))
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.test')
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Owned output.\n')
    with pytest.raises(knowledge_commit.GitError, match='complete history'):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'owned')
    assert git(origin, 'rev-parse', 'main') == git(original, 'rev-parse', 'HEAD')


def test_receipt_interruption_retains_commit_capture_and_refuses_automatic_retry(tmp_path, monkeypatch):
    import publication_history
    repo, origin = fixture(tmp_path)
    base = git(origin, 'rev-parse', 'main')
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Preserve through verification failure.\n')
    def interrupted(*args):
        raise OSError('simulated receipt write failure')
    monkeypatch.setattr(publication_history, 'record', interrupted)
    with pytest.raises(knowledge_commit.GitError):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'owned')
    committed = git(repo, 'rev-parse', 'HEAD')
    assert committed != base
    assert (repo / '.git/datacore-publication-pending.json').is_file()
    captures = git(repo, 'for-each-ref', '--format=%(objectname)', 'refs/datacore/publication-captures/').splitlines()
    assert git(repo, 'rev-parse', 'HEAD^{tree}') in captures
    monkeypatch.undo()
    with pytest.raises(knowledge_commit.GitError, match='unavailable or unresolved'):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'retry')
    assert git(origin, 'rev-parse', 'main') == base
    assert git(repo, 'rev-parse', 'HEAD') == committed
    assert (repo / 'journal/note.md').read_text() == 'Preserve through verification failure.\n'


def test_noop_cannot_mint_receipt_for_an_existing_private_commit(tmp_path):
    from publication_history import receipt_ref, scope_for
    repo, origin = fixture(tmp_path)
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('An existing unverified output.\n')
    (repo / 'private').write_text('Same commit, unrelated content.\n')
    git(repo, 'add', '--', 'journal/note.md', 'private')
    git(repo, 'commit', '-qm', 'unverified work')
    unverified = git(repo, 'rev-parse', 'HEAD')
    assert knowledge_commit.commit_to_branch(repo, 'main', ['journal/note.md'], 'no-op', push=False) == ''
    ref = receipt_ref(scope_for(repo, 'main'), unverified)
    assert git(repo, 'for-each-ref', '--format=%(refname)', ref) == ''
    with pytest.raises(knowledge_commit.GitError, match='unverified local history'):
        knowledge_commit._push_commit(repo, 'main', unverified)
    assert 'private' not in git(origin, 'ls-tree', '-r', '--name-only', 'main').splitlines()


def test_automatic_publication_does_not_initialize_a_new_remote_with_old_history(tmp_path):
    repo, origin = fixture(tmp_path)
    new = tmp_path / 'new.git'
    git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(new))
    git(repo, 'remote', 'set-url', 'origin', str(new))
    (repo / 'journal').mkdir()
    (repo / 'journal/note.md').write_text('Selected output, not authority for repository history.\n')
    with pytest.raises(knowledge_commit.GitError):
        knowledge_commit.commit_knowledge(repo, ['journal/note.md'], 'owned')
    assert git(new, 'for-each-ref', '--format=%(refname)', 'refs/heads/') == ''
    assert (repo / 'journal/note.md').exists()
    assert git(origin, 'rev-parse', 'main') != git(repo, 'rev-parse', 'HEAD')


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
