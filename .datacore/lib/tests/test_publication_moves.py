"""A missing report can only publish with its exact preserved archive."""
import subprocess

import pytest

import knowledge_commit as publication
from publication_moves import capture_source, MoveReceipt


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                          check=True).stdout


@pytest.fixture
def moved(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'config', 'core.hooksPath', str(repo / '.git/hooks'))
    source = repo / '0-inbox/report.md'
    source.parent.mkdir()
    source.write_bytes(b'---\r\ntitle: Preserved\r\n---\r\nComplete report\r\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'source')
    snapshot = capture_source(repo, '0-inbox/report.md')
    destination = repo / '0-inbox/_archive/processed/report.md'
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    receipt = snapshot.to(destination.relative_to(repo).as_posix())
    return repo, source, destination, receipt


@pytest.mark.parametrize('isolated', [False, True])
def test_publish_preserved_move_keeps_unrelated_work_and_retries(moved, isolated):
    repo, source, destination, receipt = moved
    if isolated:
        git(repo, 'switch', '-qc', 'work')
    (repo / 'neighbor.md').write_text('Unrelated staged work\n')
    git(repo, 'add', 'neighbor.md')
    before = git(repo, 'ls-files', '-s', '--', 'neighbor.md')
    sha = publication.commit_to_branch(repo, 'main', [], 'archive report', push=False, moves=[receipt])
    assert sha
    assert git(repo, 'show', f'main:{receipt.destination}') == destination.read_bytes()
    assert git(repo, 'ls-tree', 'main', '--', receipt.source) == b''
    assert git(repo, 'ls-files', '-s', '--', 'neighbor.md') == before
    assert not source.exists()
    assert publication.commit_to_branch(repo, 'main', [], 'retry', push=False, moves=[receipt]) == ''


@pytest.mark.parametrize('variant', ['archive_changed', 'archive_missing', 'source_reappeared',
                                   'source_link', 'archive_link', 'parent_link'])
def test_changed_local_move_is_refused_without_changing_refs(moved, variant):
    repo, source, destination, receipt = moved
    before = git(repo, 'rev-parse', 'HEAD')
    if variant == 'archive_changed':
        destination.write_text('Different report\n')
    elif variant == 'archive_missing':
        destination.unlink()
    elif variant == 'source_reappeared':
        source.write_text('New report\n')
    elif variant == 'source_link':
        source.symlink_to(destination)
    elif variant == 'archive_link':
        copy = destination.with_name('retained.md')
        destination.rename(copy)
        destination.symlink_to(copy)
    else:
        directory = destination.parent
        copy = directory.with_name('retained')
        directory.rename(copy)
        directory.symlink_to(copy, target_is_directory=True)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        publication.commit_to_branch(repo, 'main', [], 'refused move', push=False, moves=[receipt])
    assert git(repo, 'rev-parse', 'HEAD') == before


@pytest.mark.parametrize('location', ['source', 'destination'])
def test_independent_target_version_is_never_deleted_or_replaced(moved, location):
    repo, source, destination, receipt = moved
    source.write_text('Independent source\n')
    if location == 'source':
        git(repo, 'add', receipt.source)
    else:
        original = destination.read_bytes()
        destination.write_text('Independent archive\n')
        git(repo, 'add', receipt.destination)
        destination.write_bytes(original)
    git(repo, 'commit', '-qm', 'independent writer')
    source.unlink()
    before = git(repo, 'rev-parse', 'HEAD')
    with pytest.raises((RuntimeError, ValueError)):
        publication.commit_to_branch(repo, 'main', [], 'refused stale move', push=False, moves=[receipt])
    assert git(repo, 'rev-parse', 'HEAD') == before
    assert destination.exists()


def test_untracked_move_does_not_request_an_unrelated_deletion(moved):
    repo, source, destination, _ = moved
    source.write_text('New untracked report\n')
    extra = source.with_name('new.md')
    source.rename(extra)
    snapshot = capture_source(repo, '0-inbox/new.md')
    archive = destination.with_name('new.md')
    extra.rename(archive)
    receipt = snapshot.to(archive.relative_to(repo).as_posix())
    publication.commit_to_branch(repo, 'main', [], 'new archive', push=False, moves=[receipt])
    assert git(repo, 'show', f'main:{receipt.destination}') == b'New untracked report\n'
    assert git(repo, 'ls-tree', 'main', '--', '0-inbox/report.md')


@pytest.mark.parametrize('isolated', [False, True])
def test_normal_hooks_cannot_modify_archived_content(moved, isolated):
    repo, _, destination, receipt = moved
    if isolated:
        git(repo, 'switch', '-qc', 'work')
    hook = repo / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nprintf changed > 0-inbox/_archive/processed/report.md\n'
                    'git add -- 0-inbox/_archive/processed/report.md\n')
    hook.chmod(0o755)
    with pytest.raises(RuntimeError):
        publication.commit_to_branch(repo, 'main', [], 'hook changed archive', push=False, moves=[receipt])
    assert destination.exists()


def test_git_filter_cannot_silently_change_preserved_bytes(moved):
    repo, _, destination, receipt = moved
    (repo / '.gitattributes').write_text('0-inbox/_archive/** text eol=lf\n')
    git(repo, 'add', '.gitattributes')
    git(repo, 'commit', '-qm', 'archive filter')
    before = git(repo, 'rev-parse', 'HEAD')
    with pytest.raises((RuntimeError, ValueError)):
        publication.commit_to_branch(repo, 'main', [], 'lossy archive', push=False, moves=[receipt])
    assert git(repo, 'rev-parse', 'HEAD') == before
    assert b'\r\n' in destination.read_bytes()


@pytest.mark.parametrize('unsafe', ['../report.md', '/report.md', '.git/config'])
def test_receipt_does_not_grant_arbitrary_path_authority(moved, unsafe):
    repo, _, _, receipt = moved
    bad = MoveReceipt(unsafe, receipt.destination, receipt.source_blob, receipt.content_hash)
    with pytest.raises(RuntimeError):
        publication.commit_to_branch(repo, 'main', [], 'unsafe move', push=False, moves=[bad])


def test_move_capture_cannot_bind_old_content_to_an_intervening_commit(moved, monkeypatch):
    repo, source, destination, _ = moved
    destination.rename(source)
    capture = publication._capture

    def concurrent_commit(root, relative):
        captured = capture(root, relative)
        source.write_text('Newer independent report\n')
        git(repo, 'add', '0-inbox/report.md')
        git(repo, 'commit', '-qm', 'intervening writer')
        return captured

    monkeypatch.setattr(publication, '_capture', concurrent_commit)
    with pytest.raises(ValueError, match='commit changed'):
        capture_source(repo, '0-inbox/report.md')
    assert source.read_text() == 'Newer independent report\n'
