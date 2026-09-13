"""Owner-wide reports cannot publish into a source space or truncate a review."""
import fcntl
import os
import subprocess
import sys

import pytest

import file_utils
import intent_review


@pytest.fixture
def review(tmp_path, monkeypatch):
    root = tmp_path / 'Data'
    shared = root / '2-datacore/1-tracks/ops/Intent-Graph-Review.md'
    shared.parent.mkdir(parents=True)
    shared.write_text('Existing shared document\n')
    state = tmp_path / 'private-state'
    state.mkdir(mode=0o700)
    monkeypatch.setenv('DATACORE_STATE', str(state))
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    monkeypatch.setattr(intent_review, 'build', lambda *_: 'Private aggregate\n')
    monkeypatch.setattr(sys, 'argv', ['intent_review', '--root', str(root), '--date', '2030-01-02'])
    return root, shared, state


def test_default_report_is_private_and_preserves_existing_shared_document(review):
    root, shared, state = review
    assert intent_review.main() == 0
    assert shared.read_text() == 'Existing shared document\n'
    reports = list(state.rglob('Intent-Graph-Review.md'))
    assert len(reports) == 1
    assert reports[0].read_text() == 'Private aggregate\n'
    assert reports[0].stat().st_mode & 0o077 == 0
    assert reports[0].parent.stat().st_mode & 0o077 == 0


def test_caller_cannot_route_owner_aggregate_to_an_absolute_shared_file(review):
    _, shared, _ = review
    sys.argv += ['--out', str(shared)]
    with pytest.raises((ValueError, OSError)):
        intent_review.main()
    assert shared.read_text() == 'Existing shared document\n'


def test_failed_atomic_publication_preserves_previous_complete_review(review, monkeypatch):
    _, shared, state = review
    intent_review.main()
    before = {p: p.read_bytes() for p in state.rglob('*.md')}
    shared_before = shared.read_bytes()
    monkeypatch.setattr(intent_review, 'build', lambda *_: 'Replacement aggregate\n')
    def interrupted(fd):
        raise OSError('synthetic storage interruption')
    monkeypatch.setattr(file_utils.os, 'fsync', interrupted)
    with pytest.raises(OSError, match='storage interruption'):
        intent_review.main()
    assert all(p.read_bytes() == content for p, content in before.items())
    assert shared.read_bytes() == shared_before


@pytest.mark.parametrize('name', ['../shared.md', 'subdir/report.md', 'x\\report.md', '.hidden.md', 'report.html'])
def test_output_path_variants_are_rejected(review, name):
    sys.argv += ['--out', name]
    with pytest.raises(ValueError):
        intent_review.main()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'public'])
def test_existing_output_aliases_and_public_files_are_preserved(review, kind):
    _, _, state = review
    intent_review.main()
    target = next(state.rglob('Intent-Graph-Review.md'))
    other = state / 'retained'
    other.write_text('Retained private document\n')
    target.unlink()
    if kind == 'symlink':
        target.symlink_to(other)
    elif kind == 'hardlink':
        os.link(other, target)
    elif kind == 'fifo':
        os.mkfifo(target)
    else:
        target.write_text('Existing public-mode file\n')
        target.chmod(0o644)
    inode = target.lstat().st_ino
    with pytest.raises((ValueError, OSError)):
        intent_review.main()
    assert target.lstat().st_ino == inode
    assert other.read_text() == 'Retained private document\n'


def test_build_failure_preserves_private_review(review, monkeypatch):
    _, _, state = review
    intent_review.main()
    target = next(state.rglob('Intent-Graph-Review.md'))
    def unavailable(*args):
        raise RuntimeError('synthetic dependency failure')
    monkeypatch.setattr(intent_review, 'build', unavailable)
    with pytest.raises(RuntimeError, match='dependency failure'):
        intent_review.main()
    assert target.read_text() == 'Private aggregate\n'


def test_installed_aggregation_roots_cannot_overwrite_each_other(review):
    root, _, state = review
    intent_review.main()
    second = root.parent / 'AnotherData'
    second.mkdir()
    sys.argv[2] = str(second)
    intent_review.main()
    reports = list(state.rglob('Intent-Graph-Review.md'))
    assert len(reports) == 2
    assert reports[0].parent != reports[1].parent


def test_generation_starts_only_after_obtaining_publication_lock(review):
    root, _, state = review
    intent_review.main()
    target = next(state.rglob('Intent-Graph-Review.md'))
    marker = state / 'entered-build'
    script = '''import runpy,sys
from pathlib import Path
root,marker=sys.argv[2],Path(sys.argv[3])
ns=runpy.run_path(sys.argv[1],run_name='fixture')
def build(*args):
    marker.write_text('entered')
    return 'replacement'
ns['main'].__globals__['build']=build
sys.argv=['intent_review','--root',root,'--date','2030-01-02']
raise SystemExit(ns['main']())
'''
    lock = target.parent / ('.' + target.name + '.lock')
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                                 intent_review.__file__, str(root), str(marker)],
                                env=dict(os.environ, DATACORE_STATE=str(state)),
                                capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'timed out acquiring lock' in result.stderr
    assert not marker.exists()
    assert target.read_text() == 'Private aggregate\n'


def test_report_uses_current_counts_and_renders_source_text_as_literal(tmp_path, monkeypatch):
    from priority_score import IntentGraph, Node
    import priority_score
    import intent_tasks
    graph = IntentGraph({
        'one': Node(id='one', title='<img src="https://invalid.test/private">', level=1),
        'two': Node(id='two', title='Second intent', level=1),
    }, [{'rank': 1, 'id': 'one', 'statement': '[sensitive](https://invalid.test)'}], {})
    monkeypatch.setattr(priority_score.IntentGraph, 'load', lambda root: graph)
    monkeypatch.setattr(intent_tasks, 'place', lambda *_: {
        'index': {'one': 1}, 'total': 1,
        'by_method': {'property': 1, 'tag': 0, 'keyword': 0, 'none': 0},
        'unplaced_by_space': {},
    })
    value = intent_review.build(tmp_path, '2030-01-02')
    assert '2 intents' in value and '5 intents' not in value
    assert '1 by property' in value
    assert '0 of 1 stated priorities have no matching node' in value
    assert '<img' not in value and '[sensitive](' not in value
    assert '&lt;img' in value and r'\[sensitive\]' in value
    assert 'signed contract' not in value and 'Six other ventures' not in value
