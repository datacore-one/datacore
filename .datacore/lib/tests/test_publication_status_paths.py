"""Lossless inventory is required before deciding which paths may publish."""
import subprocess
import sys
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import commit_gate
import git_inventory
import pytest


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    git(work, 'init', '-q', '-b', 'main')
    git(work, 'config', 'user.name', 'Fixture')
    git(work, 'config', 'user.email', 'fixture@example.test')
    (work / 'seed').write_text('seed\n')
    git(work, 'add', '--', 'seed')
    git(work, 'commit', '-qm', 'seed')
    monkeypatch.setattr(commit_gate, 'PENDING', tmp_path / 'decisions')
    return work


@pytest.mark.parametrize('name', [' leading.txt', 'trailing.txt ', 'a -> b.txt',
                                       'line\nbreak.txt', 'quote".txt', 'č.txt'])
def test_exact_filename_is_allowed_and_neighbour_is_withheld(repo, name):
    (repo / name).write_text('owned output\n')
    (repo / 'unrelated').write_text('unrelated\n')
    decision = commit_gate.decide(repo, [name], task_id='fixture')
    assert decision.allowed == [name]
    assert decision.withheld == ['unrelated']


def test_rename_inventory_includes_removed_name(repo):
    git(repo, 'mv', '--', 'seed', 'new name')
    assert set(commit_gate.dirty_paths(repo)) == {'seed', 'new name'}
    decision = commit_gate.decide(repo, ['new name'], task_id='fixture')
    assert decision.allowed == ['new name']
    assert decision.withheld == ['seed']


def test_unavailable_inventory_is_not_a_clean_checkout(tmp_path):
    with pytest.raises(RuntimeError):
        commit_gate.dirty_paths(tmp_path)


def test_same_task_same_time_preserves_both_decisions(repo):
    (repo / 'first').write_text('first\n')
    first = commit_gate.decide(repo, [], task_id='fixture', at='20260912T000000')
    original = first.record.read_bytes()
    (repo / 'second').write_text('second\n')
    second = commit_gate.decide(repo, [], task_id='fixture', at='20260912T000000')
    assert first.record != second.record
    assert first.record.read_bytes() == original
    assert len(commit_gate.pending()) == 2


def test_allowed_inventory_is_not_a_claim_of_completed_publication(repo):
    (repo / 'owned').write_text('owned\n')
    (repo / 'other').write_text('other\n')
    decision = commit_gate.decide(repo, ['owned'], task_id='fixture')
    record = json.loads(decision.record.read_text())
    assert record['allowed'] == ['owned']
    assert record['publication_verified'] is False
    assert record['kind'] == 'output-inventory'
    assert 'committed' not in record
    assert git(repo, 'log', '--oneline').count(b'\n') == 1


def test_decision_timestamp_cannot_escape_storage(repo, tmp_path):
    (repo / 'output').write_text('output\n')
    decision = commit_gate.decide(repo, [], task_id='fixture', at='../outside')
    assert decision.record.parent == tmp_path / 'decisions'
    assert decision.record.stat().st_mode & 0o077 == 0


def test_concurrent_decisions_are_independent_complete_private_records(repo):
    (repo / 'output').write_text('output\n')
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda n: commit_gate.decide(
            repo, [], task_id='same-task', at='same-time'), range(24)))
    paths = [decision.record for decision in decisions]
    assert len(set(paths)) == 24
    assert len(commit_gate.pending()) == 24
    for path in paths:
        assert json.loads(path.read_text())['withheld'] == ['output']
        assert path.stat().st_mode & 0o077 == 0
    assert paths[0].parent.stat().st_mode & 0o077 == 0


def test_symlinked_decision_directory_is_refused_without_target_mutation(repo, tmp_path):
    (repo / 'output').write_text('output\n')
    outside = tmp_path / 'outside'
    outside.mkdir()
    commit_gate.PENDING.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        commit_gate.decide(repo, [], task_id='fixture')
    assert list(outside.iterdir()) == []


def test_interrupted_record_is_retained_and_never_reported_as_complete(repo, monkeypatch):
    (repo / 'output').write_text('output\n')
    def fail(*args, **kwargs):
        raise OSError('injected publication failure')
    monkeypatch.setattr(commit_gate.os, 'link', fail)
    with pytest.raises(OSError):
        commit_gate.decide(repo, [], task_id='fixture')
    assert commit_gate.pending() == []
    retained = list(commit_gate.PENDING.glob('.*.pending'))
    assert len(retained) == 1
    assert json.loads(retained[0].read_text())['withheld'] == ['output']


@pytest.mark.parametrize('raw', [b' M x', b'R  new\0', b'?? \0', b'?? x\0\0', b'xx x\0'])
def test_malformed_inventory_fails_explicitly(raw):
    with pytest.raises(RuntimeError):
        git_inventory.parse_status(raw)


def test_copy_does_not_mark_unchanged_source_for_deletion():
    change, = git_inventory.parse_status(b'C  copy\0original\0')
    assert change.source == 'original'
    assert change.changed_paths == ('copy',)


def test_non_utf8_filename_round_trips():
    # APFS refuses such filenames, but a Git inventory from a Linux checkout
    # can contain them. Verify bytes survive parsing without replacement.
    change, = git_inventory.parse_status(b'?? raw-\xff.txt\0')
    assert os.fsencode(change.path) == b'raw-\xff.txt'


def test_literal_path_filter_does_not_expand_glob(repo):
    import wrap_up_mechanics
    (repo / '*').write_text('literal star\n')
    (repo / 'other').write_text('neighbour\n')
    assert wrap_up_mechanics.git_status_entries(repo, '*') == [('??', '*')]


def test_wrap_up_inventory_failure_cannot_pass_as_clean(tmp_path):
    import wrap_up_mechanics
    with pytest.raises(RuntimeError):
        wrap_up_mechanics.git_dirty(tmp_path)


def test_fleet_sweep_cannot_include_preexisting_staged_private_file(repo, tmp_path, monkeypatch):
    import git_fleet_sync
    monkeypatch.setattr(git_fleet_sync, 'review_gate', lambda *args: '')
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '--bare', '-q', '-b', 'main', str(origin))
    git(repo, 'remote', 'add', 'origin', str(origin))
    git(repo, 'push', '-u', 'origin', 'main')
    (repo / 'scratch.local.md').write_text('private staged fixture\n')
    git(repo, 'add', '--', 'scratch.local.md')
    (repo / 'report\nč.md').write_text('valid report\n')
    result = git_fleet_sync.sync_repo(repo, execute=True)
    assert result['status'].startswith('PUSHED')
    assert git(origin, 'show', 'main:report\nč.md') == b'valid report\n'
    assert git(origin, 'ls-tree', '-r', '--name-only', '-z', 'main') == b'report\n\xc4\x8d.md\0seed\0'
    assert git(repo, 'diff', '--cached', '--name-only', '-z') == b'scratch.local.md\0'


def test_fleet_rename_requires_review(repo, monkeypatch):
    import git_fleet_sync
    monkeypatch.setattr(git_fleet_sync, 'review_gate', lambda *args: '')
    git(repo, 'mv', '--', 'seed', 'renamed')
    result = git_fleet_sync.sync_repo(repo, execute=False)
    assert result['committed'] == []
    assert any(path == 'renamed' and 'RENAME' in reason for path, reason in result['skipped'])


def test_fleet_inventory_error_is_reported_without_publication(repo, monkeypatch):
    import git_fleet_sync
    monkeypatch.setattr(git_fleet_sync, 'review_gate', lambda *args: '')
    def fail(*args):
        raise RuntimeError('injected inventory failure')
    monkeypatch.setattr(git_fleet_sync, 'working_changes', fail)
    result = git_fleet_sync.sync_repo(repo, execute=True)
    assert result['status'].startswith('INVENTORY FAILED')
    assert result['committed'] == []
