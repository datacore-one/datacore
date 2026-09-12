"""Actual Git conflicts and publication never discard the other writer's data."""
from pathlib import Path
import json
import shlex
import subprocess
import sys

import pytest

import git_relay
import knowledge_commit as knowledge
from publication_manifest import PublicationManifest
import resolve_ledger_conflicts as resolver
import resolve_cadence_log_conflict as cadence


def git(repo, *args, check=True):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / 'repository'
    path.mkdir()
    git(path, 'init', '-q', '-b', 'main')
    git(path, 'config', 'user.name', 'Audit')
    git(path, 'config', 'user.email', 'audit@example.invalid')
    git(path, 'config', 'core.hooksPath', str(tmp_path / 'empty-hooks'))
    (path / 'notes').mkdir()
    (path / 'notes/base.md').write_text('base\n')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'base')
    return path


def conflict(repo, name, base, ours, theirs, style='merge'):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base)
    git(repo, 'add', '--', name); git(repo, 'commit', '-qm', 'conflict base')
    git(repo, 'checkout', '-qb', 'incoming')
    path.write_text(theirs)
    git(repo, 'commit', '-qam', 'incoming')
    git(repo, 'checkout', '-q', 'main')
    path.write_text(ours)
    git(repo, 'commit', '-qam', 'ours')
    git(repo, 'config', 'merge.conflictStyle', style)
    assert git(repo, 'merge', '--no-edit', 'incoming', check=False).returncode == 1
    return path


@pytest.mark.parametrize('name', ['record.org', 'settings.yaml', 'notes/name\nwith-newline.md'])
def test_unknown_conflicts_and_unrelated_index_are_preserved(repo, name):
    path = conflict(repo, name, 'base\n', 'ours\n', 'theirs\n')
    (repo / 'unrelated.txt').write_text('private draft')
    git(repo, 'add', 'unrelated.txt')
    before, index, head = path.read_bytes(), git(repo, 'ls-files', '-s', '-z').stdout, git(repo, 'rev-parse', 'HEAD').stdout
    assert resolver.main([str(repo)]) == 1
    assert path.read_bytes() == before
    assert git(repo, 'ls-files', '-s', '-z').stdout == index
    assert git(repo, 'rev-parse', 'HEAD').stdout == head


def ledger(*values):
    from ledger.events import Event, body_dict, compute_hash, to_line
    rows = []; previous = 'GENESIS'
    for seq, value in enumerate(values):
        body = body_dict(seq, f'{seq+1000:013d}:000000:agent', 'agent', 'item.create', {'id': value}, previous)
        previous = compute_hash(body)
        rows.append(to_line(Event(**body, hash=previous, sig='')))
    return '\n'.join(rows) + '\n'


def test_ledger_fork_cannot_be_staged_or_committed(repo):
    path = conflict(repo, '.datacore/events/agent.jsonl', ledger('base'), ledger('base', 'ours'), ledger('base', 'theirs'))
    before, index = path.read_bytes(), git(repo, 'ls-files', '-u').stdout
    assert resolver.main([str(repo)]) == 1
    assert path.read_bytes() == before and git(repo, 'ls-files', '-u').stdout == index


def test_prefix_resolution_rejects_changed_working_content():
    short, long = ledger('base'), ledger('base', 'more')
    assert resolver.prefix_resolution(short, long, long) == long
    with pytest.raises(resolver.ForkError):
        resolver.prefix_resolution(short, long, ledger('base', 'manual-edit'))


@pytest.mark.parametrize('style', ['merge', 'diff3', 'zdiff3'])
def test_cadence_preserves_unknown_fields_and_index_evidence(repo, style):
    path = conflict(repo, 'cadence.yaml', 'job:\n  last_run: 2026-01-01\n',
                    'job:\n  last_run: 2026-02-01\n  notes: newer\n',
                    'job:\n  last_run: 2026-01-20\n  extension: retained\n', style)
    index = git(repo, 'ls-files', '-u').stdout
    cadence.resolve(repo, 'cadence.yaml')
    text = path.read_text()
    assert 'newer' in text and 'retained' in text and '2026-02-01' in text
    assert git(repo, 'ls-files', '-u').stdout == index


def test_cadence_missing_stage_and_manual_edits_are_not_overwritten(repo):
    path = repo / 'cadence.yaml'; path.write_text('valid existing content')
    with pytest.raises(ValueError): cadence.resolve(repo, 'cadence.yaml')
    assert path.read_text() == 'valid existing content'
    path = conflict(repo, 'cadence.yaml', 'job: {}\n', 'job:\n  last_run: 2026-02-01\n', 'job:\n  last_run: 2026-01-20\n')
    path.write_text(path.read_text() + 'manual: valuable\n')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='edited'): cadence.resolve(repo, 'cadence.yaml')
    assert path.read_bytes() == before


def test_equal_cadence_times_with_different_values_require_review():
    with pytest.raises(ValueError, match='same timestamp'):
        cadence.merge({'job': {'last_run':'2026-01-01', 'notes':'ours'}}, {'job': {'last_run':'2026-01-01', 'notes':'theirs'}})


def test_cross_branch_publication_keeps_working_copy(repo):
    git(repo, 'checkout', '-qb', 'feature')
    note = repo / 'notes/new.md'; note.write_text('valuable knowledge\n')
    sha = knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'knowledge', push=False)
    assert sha and note.read_text() == 'valuable knowledge\n'
    assert git(repo, 'show', 'main:notes/new.md').stdout == note.read_text()
    assert git(repo, 'branch', '--show-current').stdout.strip() == 'feature'


def test_cross_branch_retry_pushes_existing_commit_and_keeps_reporting_failure(repo, tmp_path):
    git(repo, 'checkout', '-qb', 'feature')
    path = repo / 'notes/retry.md'
    path.write_text('durable local knowledge\n')
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/retry.md'], 'first attempt')
    pending = git(repo, 'rev-parse', 'main').stdout.strip()
    # No content changed: a second unavailable-remote attempt must still fail.
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/retry.md'], 'second attempt')
    assert git(repo, 'rev-parse', 'main').stdout.strip() == pending
    remote = tmp_path / 'available.git'
    git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    assert knowledge.commit_to_branch(repo, 'main', ['notes/retry.md'], 'retry') == ''
    assert git(remote, 'rev-parse', 'main').stdout.strip() == pending
    assert path.read_text() == 'durable local knowledge\n'
    assert git(repo, 'branch', '--show-current').stdout.strip() == 'feature'


def test_cross_branch_publication_refuses_to_erase_newer_target(repo):
    git(repo, 'branch', 'feature')
    note = repo / 'notes/base.md'; note.write_text('new main knowledge\n')
    git(repo, 'commit', '-qam', 'new main')
    git(repo, 'checkout', '-q', 'feature'); note.write_text('feature edit based on older text\n')
    before = git(repo, 'rev-parse', 'main').stdout
    with pytest.raises(knowledge.GitError, match='independent changes'):
        knowledge.commit_to_branch(repo, 'main', ['notes/base.md'], 'knowledge', push=False)
    assert git(repo, 'rev-parse', 'main').stdout == before
    assert note.read_text() == 'feature edit based on older text\n'


def test_recorded_publication_does_not_include_unrelated_staged_data(repo):
    manifest = PublicationManifest(repo)
    note = repo / 'notes/new.md'; note.write_text('own output'); manifest.record(note, 'own output')
    (repo / 'unrelated.txt').write_text('private draft'); git(repo, 'add', 'unrelated.txt')
    assert manifest.publish('own output', push=False)
    assert git(repo, 'show', '--format=', '--name-only', 'HEAD').stdout.strip() == 'notes/new.md'
    assert git(repo, 'diff', '--cached', '--name-only').stdout.strip() == 'unrelated.txt'
    assert (repo / 'unrelated.txt').read_text() == 'private draft'


def test_recorded_publication_refuses_stale_output_and_initial_dirty_state(repo):
    path = repo / 'notes/base.md'; path.write_text('preexisting edit')
    manifest = PublicationManifest(repo); manifest.record(path, 'preexisting edit')
    with pytest.raises(knowledge.GitError, match='clean checkout'): manifest.publish('refused', push=False)
    git(repo, 'commit', '-qam', 'user edit')
    manifest = PublicationManifest(repo); path.write_text('own output'); manifest.record(path, 'own output')
    path.write_text('newer user edit')
    with pytest.raises(knowledge.GitError, match='changed after'): manifest.publish('refused', push=False)
    assert path.read_text() == 'newer user edit'


def test_publication_error_preserves_commit_and_never_claims_push_success(repo):
    manifest = PublicationManifest(repo)
    path = repo / 'notes/output.md'; path.write_text('data'); manifest.record(path, 'data')
    with pytest.raises(knowledge.GitError): manifest.publish('no remote')
    assert git(repo, 'show', 'HEAD:notes/output.md').stdout == 'data'
    assert path.read_text() == 'data'


@pytest.mark.parametrize('path', ['../outside.md', '/tmp/outside.md'])
def test_publication_paths_cannot_escape_repository(repo, path):
    with pytest.raises(knowledge.GitError, match='escapes'):
        knowledge.commit_to_branch(repo, 'main', [path], 'refused', push=False)


def test_publication_filename_is_literal_not_a_git_pattern(repo):
    wanted = repo / 'notes/report*.md'; wanted.write_text('selected')
    (repo / 'notes/report-other.md').write_text('unrelated private text')
    knowledge.commit_to_branch(repo, 'main', ['notes/report*.md'], 'literal filename', push=False)
    assert git(repo, 'show', 'HEAD:notes/report*.md').stdout == 'selected'
    assert git(repo, 'cat-file', '-e', 'HEAD:notes/report-other.md', check=False).returncode != 0


@pytest.mark.parametrize('hook_setting', ['default', 'relative', 'absolute'])
def test_cross_branch_publication_enforces_configured_commit_hooks(repo, tmp_path, hook_setting):
    git(repo, 'checkout', '-qb', 'feature')
    hooks = repo / '.git/hooks' if hook_setting != 'absolute' else tmp_path / 'hooks'
    hooks.mkdir(exist_ok=True)
    if hook_setting == 'default':
        git(repo, 'config', '--unset', 'core.hooksPath')
    else:
        git(repo, 'config', 'core.hooksPath', '.git/hooks' if hook_setting == 'relative' else str(hooks))
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
    note = repo / 'notes/new.md'; note.write_text('valuable unpublished output\n')
    (repo / 'other.txt').write_text('independent staged output\n'); git(repo, 'add', 'other.txt')
    before = (git(repo, 'rev-parse', 'main').stdout, git(repo, 'rev-parse', 'HEAD').stdout,
              (repo / '.git/index').read_bytes())
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'hook must reject', push=False)
    assert (git(repo, 'rev-parse', 'main').stdout, git(repo, 'rev-parse', 'HEAD').stdout,
            (repo / '.git/index').read_bytes()) == before
    assert note.read_text() == 'valuable unpublished output\n'


def test_cross_branch_publishes_the_validated_bytes_not_a_later_symlink(repo, tmp_path, monkeypatch):
    git(repo, 'checkout', '-qb', 'feature')
    note = repo / 'notes/new.md'; note.write_text('captured output\n')
    outside = tmp_path / 'private.txt'; outside.write_text('unrelated private bytes\n')
    original = knowledge._git
    changed = []
    def race(path, *args, **kwargs):
        if 'hash-object' in args and not changed:
            note.unlink(); note.symlink_to(outside); changed.append(True)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(knowledge, '_git', race)
    knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'capture once', push=False)
    assert changed, 'the race must occur after source capture and before hashing'
    assert git(repo, 'show', 'main:notes/new.md').stdout == 'captured output\n'
    assert note.is_symlink() and outside.read_text() == 'unrelated private bytes\n'


def test_cross_branch_refuses_a_destination_checked_out_elsewhere(repo, tmp_path):
    git(repo, 'checkout', '-qb', 'feature')
    other = tmp_path / 'other-writer'
    git(repo, 'worktree', 'add', str(other), 'main')
    note = repo / 'notes/new.md'; note.write_text('own output\n')
    (other / 'notes/base.md').write_text('another writer has unsaved work\n')
    before = git(repo, 'rev-parse', 'main').stdout
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'do not move their HEAD', push=False)
    assert git(repo, 'rev-parse', 'main').stdout == before
    assert (other / 'notes/base.md').read_text() == 'another writer has unsaved work\n'


@pytest.mark.parametrize('change', ['extra-file', 'changed-output', 'changed-parent'])
def test_cross_branch_hook_cannot_change_the_published_tree_or_parent(repo, tmp_path, change):
    git(repo, 'checkout', '-qb', 'feature')
    hooks = tmp_path / 'checks'; hooks.mkdir()
    git(repo, 'config', 'core.hooksPath', str(hooks))
    if change == 'changed-parent':
        body = 'git -c core.hooksPath=/dev/null commit --allow-empty -m unexpected-parent\n'
    elif change == 'extra-file':
        body = 'echo unexpected > extra.txt\ngit add extra.txt\n'
    else:
        body = 'echo changed > notes/new.md\ngit add notes/new.md\n'
    hook = hooks / 'pre-commit'; hook.write_text('#!/bin/sh\n'+body); hook.chmod(0o755)
    note = repo / 'notes/new.md'; note.write_text('captured output\n')
    before = git(repo, 'rev-parse', 'main').stdout
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'checked tree', push=False)
    assert git(repo, 'rev-parse', 'main').stdout == before
    assert note.read_text() == 'captured output\n'
    assert git(repo, 'branch', '--show-current').stdout.strip() == 'feature'


def test_cross_branch_rechecks_destination_and_reserves_against_checkout(repo, tmp_path, monkeypatch):
    git(repo, 'checkout', '-qb', 'feature')
    note = repo / 'notes/new.md'; note.write_text('own output\n')
    base = git(repo, 'rev-parse', 'main').stdout.strip()
    new_tip = git(repo, 'commit-tree', f'{base}^{{tree}}', '-p', base, '-m', 'other writer').stdout.strip()
    original = knowledge._git
    races = []
    def advance(path, *args, **kwargs):
        if args[:2] == ('update-ref', 'refs/heads/main'):
            checkout = git(repo, 'worktree', 'add', str(tmp_path / 'competing-checkout'), 'main', check=False)
            assert checkout.returncode != 0, 'Git must reserve the target during publication'
            git(repo, 'update-ref', 'refs/heads/main', new_tip, base)
            races.append(True)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(knowledge, '_git', advance)
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'stale base', push=False)
    assert races and git(repo, 'rev-parse', 'main').stdout.strip() == new_tip
    assert note.read_text() == 'own output\n'
    git(repo, 'worktree', 'add', str(tmp_path / 'released-checkout'), 'main')


def test_cross_branch_push_uses_captured_commit_even_if_local_branch_advances(repo, tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'; git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote)); git(repo, 'push', 'origin', 'main')
    git(repo, 'checkout', '-qb', 'feature')
    note = repo / 'notes/new.md'; note.write_text('own output\n')
    original = knowledge._git
    advanced = []
    def race(path, *args, **kwargs):
        if args[0] == 'push':
            base = git(repo, 'rev-parse', 'main').stdout.strip()
            other = git(repo, 'commit-tree', f'{base}^{{tree}}', '-p', base, '-m', 'not authorized by this call').stdout.strip()
            git(repo, 'update-ref', 'refs/heads/main', other, base); advanced.append(other)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(knowledge, '_git', race)
    published = knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'exact source')
    assert advanced and git(repo, 'rev-parse', 'main').stdout.strip() == advanced[0]
    assert git(remote, 'rev-parse', 'main').stdout.strip() == published


def test_cross_branch_ignored_hook_hardlink_cannot_truncate_external_file(repo, tmp_path):
    (repo / '.gitignore').write_text('notes/new.md\n')
    git(repo, 'add', '.gitignore'); git(repo, 'commit', '-qm', 'ignore generated notes')
    git(repo, 'checkout', '-qb', 'feature')
    hooks = tmp_path / 'checks'; hooks.mkdir()
    outside = tmp_path / 'independent-file'; outside.write_text('independent data\n')
    hook = hooks / 'post-checkout'
    hook.write_text('#!/bin/sh\nln '+shlex.quote(str(outside))+' notes/new.md\n'); hook.chmod(0o755)
    git(repo, 'config', 'core.hooksPath', str(hooks))
    note = repo / 'notes/new.md'; note.write_text('published output\n')
    knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'no truncation', push=False)
    assert outside.read_text() == 'independent data\n'
    assert git(repo, 'show', 'main:notes/new.md').stdout == 'published output\n'


def test_cross_branch_non_utf8_hook_error_is_sanitized_and_retains_late_output(repo, tmp_path):
    git(repo, 'checkout', '-qb', 'feature')
    hooks = tmp_path / 'checks'; hooks.mkdir()
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\nprintf "\\377 private-output" >&2\necho recover > ignored-recovery\nexit 1\n')
    hook.chmod(0o755); git(repo, 'config', 'core.hooksPath', str(hooks))
    note = repo / 'notes/new.md'; note.write_text('own output\n')
    with pytest.raises(knowledge.GitError) as error:
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'refused', push=False)
    assert 'private-output' not in str(error.value)
    worktrees = git(repo, 'worktree', 'list', '--porcelain').stdout.splitlines()
    retained = [Path(line[9:]) for line in worktrees if line.startswith('worktree ')]
    assert any((path / 'ignored-recovery').read_text() == 'recover\n'
               for path in retained if (path / 'ignored-recovery').exists())
    assert note.read_text() == 'own output\n'


@pytest.mark.parametrize('branch', ['main', 'feature'])
@pytest.mark.parametrize('path', ['notes/missing.md', 'notes'])
def test_publication_missing_or_non_file_output_is_not_acknowledged(repo, branch, path):
    if branch == 'feature':
        git(repo, 'checkout', '-qb', branch)
    before = git(repo, 'rev-parse', 'main').stdout
    with pytest.raises(knowledge.GitError, match='missing or not a regular file'):
        knowledge.commit_to_branch(repo, 'main', [path], 'no silent omission', push=False)
    assert git(repo, 'rev-parse', 'main').stdout == before


def test_same_branch_rejecting_hook_cannot_claim_there_is_nothing_to_commit(repo, tmp_path):
    hooks = tmp_path / 'checks'; hooks.mkdir()
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\nprintf "nothing to commit\\n\\377 private-output" >&2\nexit 1\n')
    hook.chmod(0o755); git(repo, 'config', 'core.hooksPath', str(hooks))
    (repo / 'notes/new.md').write_text('unpublished output\n')
    before = git(repo, 'rev-parse', 'main').stdout
    with pytest.raises(knowledge.GitError) as error:
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'must refuse', push=False)
    assert 'private-output' not in str(error.value)
    assert git(repo, 'rev-parse', 'main').stdout == before


@pytest.mark.parametrize('change', ['extra-file', 'changed-output', 'changed-parent'])
def test_same_branch_hooks_cannot_expand_acknowledged_publication(repo, tmp_path, change):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', 'origin', 'main')
    before = git(remote, 'rev-parse', 'refs/heads/main').stdout
    hooks = tmp_path / 'checks'
    hooks.mkdir()
    git(repo, 'config', 'core.hooksPath', str(hooks))
    if change == 'changed-parent':
        body = 'git -c core.hooksPath=/dev/null commit --allow-empty -m unexpected-parent\n'
    elif change == 'extra-file':
        body = 'echo private-draft > extra.txt\ngit add extra.txt\n'
    else:
        body = 'echo changed > notes/new.md\ngit add notes/new.md\n'
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\n' + body)
    hook.chmod(0o755)
    (repo / 'notes/new.md').write_text('declared output\n')
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'declared publication')
    assert git(remote, 'rev-parse', 'refs/heads/main').stdout == before


def test_retry_cannot_publish_a_previously_rejected_hook_commit(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', 'origin', 'main')
    before = git(remote, 'rev-parse', 'refs/heads/main').stdout
    hooks = tmp_path / 'checks'
    hooks.mkdir()
    git(repo, 'config', 'core.hooksPath', str(hooks))
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\necho private-draft > extra.txt\ngit add extra.txt\n')
    hook.chmod(0o755)
    (repo / 'notes/new.md').write_text('declared output\n')
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'first attempt')
    hook.unlink()
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'retry')
    assert git(remote, 'rev-parse', 'refs/heads/main').stdout == before
    assert (repo / 'extra.txt').read_text() == 'private-draft\n'


def test_crash_after_commit_keeps_publication_unverified_across_processes(repo, tmp_path):
    from git_inventory import require_resolved
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', 'origin', 'main')
    before = git(remote, 'rev-parse', 'refs/heads/main').stdout
    (repo / 'notes/new.md').write_text('survives interruption\n')
    script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import knowledge_commit as knowledge
original = knowledge._git
def interrupted(repo, *args, **kwargs):
    result = original(repo, *args, **kwargs)
    if args[0] == 'commit':
        os._exit(73)
    return result
knowledge._git = interrupted
knowledge.commit_to_branch(Path(sys.argv[1]), 'main', ['notes/new.md'], 'interrupted')
'''
    result = subprocess.run([sys.executable, '-c', script, str(repo), str(Path(knowledge.__file__).parent)],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr
    with pytest.raises(RuntimeError, match='Unverified publication'):
        require_resolved(repo)
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'retry after crash')
    assert git(remote, 'rev-parse', 'refs/heads/main').stdout == before
    assert git(repo, 'show', 'HEAD:notes/new.md').stdout == 'survives interruption\n'
    assert (repo / 'notes/new.md').read_text() == 'survives interruption\n'


@pytest.mark.parametrize('hook_exit', [0, 1])
def test_rejected_capture_survives_restaging_and_git_pruning(repo, tmp_path, hook_exit):
    note = repo / 'notes/new.md'
    note.write_text('Original valid output.\n')
    hooks = tmp_path / 'checks'
    hooks.mkdir()
    git(repo, 'config', 'core.hooksPath', str(hooks))
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\necho replaced > notes/new.md\ngit add notes/new.md\n'
                    f'exit {hook_exit}\n')
    hook.chmod(0o755)
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'attempt', push=False)
    if hook_exit == 0:
        record_path = repo / '.git/datacore-publication-pending.json'
    else:
        assert not (repo / '.git/datacore-publication-pending.json').exists()
        records = list((repo / '.git/datacore-publication-workspaces').glob('*/publication-intent.json'))
        assert len(records) == 1
        record_path = records[0]
    record = json.loads(record_path.read_text())
    git(repo, 'add', '--', 'notes/new.md')
    git(repo, 'prune', '--expire=now')
    assert git(repo, 'show', record['expected_ref'] + ':notes/new.md').stdout == 'Original valid output.\n'
    assert note.read_text() == 'replaced\n'


def test_normal_commit_hook_observes_durable_git_options_despite_unsafe_defaults(repo, tmp_path):
    git(repo, 'config', 'core.fsync', 'none')
    git(repo, 'config', 'core.fsyncMethod', 'writeout-only')
    hooks = tmp_path / 'checks'
    hooks.mkdir()
    git(repo, 'config', 'core.hooksPath', str(hooks))
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\n'
                    'test "$(git config core.fsync)" = committed,reference || exit 41\n'
                    'test "$(git config core.fsyncMethod)" = fsync || exit 42\n')
    hook.chmod(0o755)
    (repo / 'notes/new.md').write_text('Explicitly flushed publication.\n')
    commit = knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'durable', push=False)
    assert git(repo, 'show', f'{commit}:notes/new.md').stdout == 'Explicitly flushed publication.\n'
    assert git(repo, 'config', '--local', 'core.fsync').stdout.strip() == 'none'
    assert git(repo, 'config', '--local', 'core.fsyncMethod').stdout.strip() == 'writeout-only'


def test_pending_publication_blocks_other_linked_worktree_publishers(repo, tmp_path):
    from publication_state import reserve
    from git_inventory import require_resolved
    other = tmp_path / 'other-checkout'
    git(repo, 'worktree', 'add', '-b', 'other', str(other))
    with reserve(repo, 'main', ['notes/base.md']):
        with pytest.raises(RuntimeError, match='Unverified publication'):
            require_resolved(other)
        with pytest.raises(FileExistsError):
            with reserve(other, 'other', ['notes/base.md']):
                pytest.fail('another publisher entered the reserved repository')
    require_resolved(other)  # a no-change attempt releases only its own record


@pytest.mark.parametrize('branch', ['main', 'feature'])
def test_publication_respects_disabled_filesystem_mode_tracking(repo, branch):
    git(repo, 'config', 'core.filemode', 'false')
    if branch == 'feature':
        git(repo, 'checkout', '-qb', branch)
    output = repo / 'notes/new.md'
    output.write_text('valid output on a filesystem without trusted executable bits\n')
    output.chmod(0o755)
    sha = knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'content publication', push=False)
    assert git(repo, 'ls-tree', sha, '--', 'notes/new.md').stdout.startswith('100644 ')
    assert output.read_text().startswith('valid output')


def diverged_publication(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', 'origin', 'main')
    git(repo, 'checkout', '-qb', 'remote-writer')
    (repo / 'notes/remote.md').write_text('Remote writer content.\n')
    git(repo, 'add', '--', 'notes/remote.md')
    git(repo, 'commit', '-qm', 'remote work')
    remote_tip = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    git(repo, 'push', 'origin', f'{remote_tip}:refs/heads/main')
    git(repo, 'checkout', '-q', 'main')
    (repo / 'notes/local.md').write_text('Captured local content.\n')
    git(repo, 'add', '--', 'notes/local.md')
    git(repo, 'commit', '-qm', 'captured work')
    return remote, remote_tip, git(repo, 'rev-parse', 'HEAD').stdout.strip()


def test_convergence_hooks_cannot_expand_the_remote_publication(repo, tmp_path):
    remote, remote_tip, captured = diverged_publication(repo, tmp_path)
    before_index = (repo / '.git/index').read_bytes()
    hooks = tmp_path / 'checks'
    hooks.mkdir()
    git(repo, 'config', 'core.hooksPath', str(hooks))
    hook = hooks / 'pre-merge-commit'
    hook.write_text('#!/bin/sh\necho private-draft > extra.txt\ngit add extra.txt\n')
    hook.chmod(0o755)
    with pytest.raises(RuntimeError):
        knowledge._push_converging(repo, 'main', captured)
    assert git(remote, 'rev-parse', 'refs/heads/main').stdout.strip() == remote_tip
    assert git(repo, 'rev-parse', 'HEAD').stdout.strip() == captured
    assert (repo / '.git/index').read_bytes() == before_index
    assert not (repo / 'extra.txt').exists()


def test_convergence_preserves_both_histories_and_the_dirty_source_checkout(repo, tmp_path):
    remote, remote_tip, captured = diverged_publication(repo, tmp_path)
    (repo / 'unrelated').write_text('Uncommitted private content.\n')
    git(repo, 'add', '--', 'unrelated')
    before_index = (repo / '.git/index').read_bytes()
    knowledge._push_converging(repo, 'main', captured)
    assert git(remote, 'show', '-s', '--format=%P', 'main').stdout.split() == [remote_tip, captured]
    assert git(remote, 'show', 'main:notes/remote.md').stdout == 'Remote writer content.\n'
    assert git(remote, 'show', 'main:notes/local.md').stdout == 'Captured local content.\n'
    assert git(remote, 'cat-file', '-e', 'main:unrelated', check=False).returncode != 0
    assert git(repo, 'rev-parse', 'HEAD').stdout.strip() == captured
    assert (repo / '.git/index').read_bytes() == before_index
    assert (repo / 'unrelated').read_text() == 'Uncommitted private content.\n'


def test_same_branch_late_head_cannot_replace_the_acknowledged_commit(repo, tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    git(repo, 'init', '--bare', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', 'origin', 'main')
    before = git(remote, 'rev-parse', 'refs/heads/main').stdout
    (repo / 'notes/new.md').write_text('declared output\n')
    original = knowledge._git
    advanced = []

    def race(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if args[0] == 'commit':
            git(repo, 'commit', '--allow-empty', '-m', 'another writer')
            advanced.append(True)
        return result

    monkeypatch.setattr(knowledge, '_git', race)
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'declared publication')
    assert advanced
    assert git(remote, 'rev-parse', 'refs/heads/main').stdout == before


@pytest.mark.parametrize('branch', ['main', 'feature'])
def test_publication_ignores_inherited_repository_and_index_selectors(repo, tmp_path, monkeypatch, branch):
    if branch == 'feature':
        git(repo, 'checkout', '-qb', branch)
    other = tmp_path / 'unrelated-repository'; other.mkdir()
    git(other, 'init', '-qb', 'main')
    (repo / 'notes/new.md').write_text('own output\n')
    with monkeypatch.context() as environment:
        environment.setenv('GIT_DIR', str(other / '.git'))
        environment.setenv('GIT_WORK_TREE', str(other))
        environment.setenv('GIT_INDEX_FILE', str(other / '.git/index'))
        sha = knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'intended repo', push=False)
    assert sha == git(repo, 'rev-parse', 'main').stdout.strip()
    assert git(other, 'rev-parse', '--verify', 'HEAD', check=False).returncode != 0
    assert not (other / '.git/index').exists()


@pytest.mark.parametrize('filter_kind', ['eol', 'custom'])
def test_cross_branch_capture_preserves_git_filters_and_retry_idempotency(repo, filter_kind):
    attr = 'text eol=lf' if filter_kind == 'eol' else 'filter=marker'
    (repo / '.gitattributes').write_text('notes/*.md '+attr+'\n')
    git(repo, 'add', '.gitattributes'); git(repo, 'commit', '-qm', 'attributes')
    if filter_kind == 'custom':
        git(repo, 'config', 'filter.marker.clean', 'sed s/WORK:/CANON:/g')
        git(repo, 'config', 'filter.marker.smudge', 'cat')
        git(repo, 'config', 'filter.marker.required', 'true')
    git(repo, 'checkout', '-qb', 'feature')
    path = repo / 'notes/new.md'
    raw = b'line\r\n' if filter_kind == 'eol' else b'WORK: output\n'
    canonical = 'line\n' if filter_kind == 'eol' else 'CANON: output\n'
    path.write_bytes(raw)
    sha = knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'canonical content', push=False)
    assert git(repo, 'show', f'{sha}:notes/new.md').stdout == canonical
    assert knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'retry', push=False) == ''
    assert path.read_bytes() == raw


def test_cross_branch_required_filter_failure_preserves_destination(repo):
    (repo / '.gitattributes').write_text('notes/*.new filter=marker\n')
    git(repo, 'add', '.gitattributes'); git(repo, 'commit', '-qm', 'required filter')
    git(repo, 'config', 'filter.marker.clean', 'false')
    git(repo, 'config', 'filter.marker.required', 'true')
    git(repo, 'checkout', '-qb', 'feature')
    path = repo / 'notes/output.new'; path.write_bytes(b'preserve these bytes')
    before = git(repo, 'rev-parse', 'main').stdout
    with pytest.raises(knowledge.GitError):
        knowledge.commit_to_branch(repo, 'main', ['notes/output.new'], 'must refuse', push=False)
    assert git(repo, 'rev-parse', 'main').stdout == before
    assert path.read_bytes() == b'preserve these bytes'


def test_publication_recovery_uses_persistent_private_git_storage(repo, tmp_path, monkeypatch):
    import worktree_lifecycle
    volatile = tmp_path / 'os-temporary'; volatile.mkdir()
    monkeypatch.setattr(worktree_lifecycle.tempfile, 'tempdir', str(volatile))
    parent = worktree_lifecycle.allocate_publication_workspace(repo)
    assert parent.is_relative_to(repo / '.git/datacore-publication-workspaces')
    assert parent.stat().st_mode & 0o077 == 0
    assert parent.parent.stat().st_mode & 0o077 == 0
    git(repo, 'checkout', '-qb', 'feature')
    (repo / 'notes/new.md').write_text('output\n')
    knowledge.commit_to_branch(repo, 'main', ['notes/new.md'], 'retained safely', push=False)
    worktrees = git(repo, 'worktree', 'list', '--porcelain').stdout.splitlines()
    retained = [Path(line[9:]) for line in worktrees if line.startswith('worktree ') and Path(line[9:]) != repo]
    assert retained and all(path.is_relative_to(parent.parent) for path in retained)
    assert not list(volatile.iterdir())


@pytest.mark.parametrize('unsafe', ['symlink', 'shared'])
def test_publication_recovery_refuses_untrusted_directory(repo, tmp_path, unsafe):
    import worktree_lifecycle
    root = repo / '.git/datacore-publication-workspaces'
    if unsafe == 'symlink':
        outside = tmp_path / 'outside'; outside.mkdir()
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(mode=0o755)
        root.chmod(0o755)
    with pytest.raises(RuntimeError, match='private owner permissions'):
        worktree_lifecycle.allocate_publication_workspace(repo)


def test_linked_worktrees_share_one_persistent_recovery_root(repo, tmp_path):
    import worktree_lifecycle
    linked = tmp_path / 'linked'
    git(repo, 'worktree', 'add', '--detach', str(linked), 'HEAD')
    first = worktree_lifecycle.allocate_publication_workspace(repo)
    second = worktree_lifecycle.allocate_publication_workspace(linked)
    assert first.parent == second.parent and first != second


@pytest.mark.parametrize('stage', ['host', 'origin'])
def test_relay_preserves_forked_history_without_reset_or_push(repo, monkeypatch, stage):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        out = 'main\n' if 'branch' in command else '2\n' if 'rev-list' in command else ''
        return subprocess.CompletedProcess(command, 0, out, '')
    checks = iter([['bad']] if stage == 'host' else [[], ['bad']])
    monkeypatch.setattr(git_relay, '_run', run)
    monkeypatch.setattr(git_relay, '_local_clone_for', lambda *a: repo)
    ssh = []
    monkeypatch.setattr(git_relay, '_ssh', lambda host, script: ssh.append(script) or subprocess.CompletedProcess([], 0, 'remote', ''))
    monkeypatch.setattr(git_relay, 'ledger_forks', lambda *a: next(checks))
    result = git_relay.relay('owned-host', '/tmp/root with spaces', '1-repo;touch-payload', repo.parent)
    assert 'REFUSED' in result
    assert not any('reset' in call or 'push' in call for call in calls)
    assert shlex.split(ssh[0])[2] == '/tmp/root with spaces/1-repo;touch-payload'


def test_relay_integrity_does_not_ignore_malformed_records(repo):
    path = repo / '.datacore/events/agent.jsonl'; path.parent.mkdir(parents=True)
    path.write_text('{malformed}\n')
    assert git_relay.ledger_forks(repo)


def test_concurrent_wrapup_journal_appends_preserve_every_entry(repo):
    from concurrent.futures import ThreadPoolExecutor
    from agent_wrap_up import write_journal
    def append(number):
        return write_journal(repo, f'agent{number}', f'entry-{number}', '2026-09-11', '12:00')
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(append, range(16)))
    text = paths[0].read_text()
    for number in range(16):
        assert text.count(f'entry-{number}\n') == 1


def test_wrapup_dry_run_cannot_create_a_journal(repo):
    from agent_wrap_up import wrap_up
    path = repo / 'notes/new.md'; path.write_text('unpublished')
    head = git(repo, 'rev-parse', 'HEAD').stdout
    report = wrap_up(repo, 'audit', 'session', 'dry-run summary', {}, dry_run=True)
    assert report['spaces'] and not (repo / 'journal').exists()
    assert git(repo, 'rev-parse', 'HEAD').stdout == head
    assert path.read_text() == 'unpublished'
