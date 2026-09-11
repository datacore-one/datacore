"""Actual Git conflicts and publication never discard the other writer's data."""
from pathlib import Path
import shlex
import subprocess

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
