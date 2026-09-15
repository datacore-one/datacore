#!/usr/bin/env python3
"""Per-task worktree isolation (DIP-0046 E4).

The property under test is not "a worktree gets created" — it is that a run
which CANNOT be isolated fails instead of quietly using the shared tree. Silent
degradation to the source checkout is indistinguishable from success until two
runs corrupt each other, so every failure path here asserts that no usable
workspace was handed back.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from agent_workspace import IsolationError, cleanup, create  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    r = tmp_path / "src"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    git(r, "config", "core.hooksPath", str(r / ".git" / "hooks"))
    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "seed")
    return r


def test_creates_an_isolated_checkout(source: Path, tmp_path: Path):
    ws = create(source, "task-1", root=tmp_path / "wt")
    assert ws.path.is_dir() and ws.path != source
    assert ws.branch == "agent/task-1"
    assert (ws.path / "seed.txt").exists()


def test_two_runs_do_not_see_each_others_edits(source: Path, tmp_path: Path):
    """The actual point: concurrent writers cannot read half-written files."""
    a = create(source, "task-a", root=tmp_path / "wt")
    b = create(source, "task-b", root=tmp_path / "wt")
    (a.path / "seed.txt").write_text("A is halfway through this\n")
    assert (b.path / "seed.txt").read_text() == "seed\n"
    assert (source / "seed.txt").read_text() == "seed\n"


def test_relative_hooks_cannot_silently_disappear_in_an_agent_checkout(source, tmp_path):
    git(source, 'config', 'core.hooksPath', '.git/hooks')
    with pytest.raises(IsolationError, match='absolute.*hooks|hooks.*absolute'):
        create(source, 'relative-hook', root=tmp_path / 'wt')
    assert not (tmp_path / 'wt/relative-hook').exists()


@pytest.mark.parametrize('configuration', ['absolute', 'default'])
def test_agent_commit_retains_rejecting_hooks(source, tmp_path, configuration):
    if configuration == 'default':
        git(source, 'config', '--unset', 'core.hooksPath')
    hook = source / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o700)
    ws = create(source, 'hooked', root=tmp_path / 'wt')
    before = git(ws.path, 'rev-parse', 'HEAD')
    (ws.path / 'seed.txt').write_text('unapproved edit\n')
    result = subprocess.run(['git', '-C', str(ws.path), 'commit', '-qam', 'blocked'], capture_output=True)
    assert result.returncode != 0
    assert git(ws.path, 'rev-parse', 'HEAD') == before
    assert (ws.path / 'seed.txt').read_text() == 'unapproved edit\n'


def test_collision_raises_and_yields_no_workspace(source: Path, tmp_path: Path):
    """A duplicate task id means two runs or a dead run — never a suffix."""
    create(source, "dup", root=tmp_path / "wt")
    with pytest.raises(IsolationError) as e:
        create(source, "dup", root=tmp_path / "wt")
    assert "already exists" in str(e.value)


def test_failure_never_returns_the_source_checkout(source: Path, tmp_path: Path):
    """Whatever goes wrong, the caller must not end up holding the shared tree."""
    create(source, "held", root=tmp_path / "wt")
    try:
        ws = create(source, "held", root=tmp_path / "wt")
    except IsolationError:
        ws = None
    assert ws is None            # not a Workspace pointing at `source`


def test_unusable_task_id_is_refused(source: Path, tmp_path: Path):
    for bad in ("", "has/slash", " leading"):
        with pytest.raises(IsolationError):
            create(source, bad, root=tmp_path / "wt")


def test_cleanup_keeps_a_branch_that_holds_work(source: Path, tmp_path: Path):
    ws = create(source, "with-work", root=tmp_path / "wt")
    (ws.path / "out.md").write_text("deliverable\n")
    git(ws.path, "add", "-A")
    git(ws.path, "commit", "-qm", "work")
    msg = cleanup(ws)
    assert "kept" in msg
    assert "agent/with-work" in git(source, "branch", "--list", "agent/with-work")
    assert not ws.path.exists()


def test_cleanup_removes_an_empty_branch(source: Path, tmp_path: Path):
    ws = create(source, "no-work", root=tmp_path / "wt")
    msg = cleanup(ws)
    assert "removed" in msg
    assert git(source, "branch", "--list", "agent/no-work").strip() == ""


def test_cleanup_frees_the_id_for_reuse(source: Path, tmp_path: Path):
    """An id must be usable again after a clean finish, or retries break."""
    cleanup(create(source, "recycle", root=tmp_path / "wt"))
    ws = create(source, "recycle", root=tmp_path / "wt")
    assert ws.path.is_dir()


@pytest.mark.parametrize('kind', ['tracked', 'untracked', 'ignored'])
def test_cleanup_preserves_uncommitted_files(source, tmp_path, kind):
    ws = create(source, 'unfinished', root=tmp_path / 'wt')
    path = ws.path / ('seed.txt' if kind == 'tracked' else 'draft.txt')
    if kind == 'ignored':
        (source / '.git/info/exclude').write_text('draft.txt\n')
    path.write_text('valuable unfinished work\n')
    with pytest.raises(IsolationError):
        cleanup(ws)
    assert path.read_text() == 'valuable unfinished work\n'
    assert git(source, 'rev-parse', '--verify', ws.branch).strip()


def test_cleanup_preserves_on_history_failure(source, tmp_path, monkeypatch):
    import agent_workspace
    ws = create(source, 'uncertain', root=tmp_path / 'wt')
    original = agent_workspace._git
    def fail_history(repo, *args, **kwargs):
        if args[0] == 'rev-list':
            return 1, '', 'cannot read object'
        return original(repo, *args, **kwargs)
    monkeypatch.setattr(agent_workspace, '_git', fail_history)
    with pytest.raises(IsolationError):
        cleanup(ws)
    assert ws.path.exists()
    assert git(source, 'rev-parse', '--verify', ws.branch).strip()


def test_cleanup_cannot_force_delete_unmerged_commits(source, tmp_path):
    ws = create(source, 'keep-commits', root=tmp_path / 'wt')
    (ws.path / 'seed.txt').write_text('new committed work\n')
    git(ws.path, 'commit', '-qam', 'work')
    tip = git(source, 'rev-parse', ws.branch)
    with pytest.raises(IsolationError):
        cleanup(ws, keep_branch_if_commits=False)
    assert ws.path.exists()
    assert git(source, 'rev-parse', ws.branch) == tip


@pytest.mark.parametrize('late_edit', [False, True])
def test_retirement_failure_or_late_write_never_deletes_data(source, tmp_path, monkeypatch, late_edit):
    import worktree_lifecycle
    ws = create(source, 'retained', root=tmp_path / 'wt')
    original = worktree_lifecycle._git
    def interrupt_move(repo, *args):
        if args[:2] == ('worktree', 'move'):
            if not late_edit:
                return subprocess.CompletedProcess(args, 1, '', 'move failed')
            (ws.path / 'seed.txt').write_text('late writer data\n')
        return original(repo, *args)
    monkeypatch.setattr(worktree_lifecycle, '_git', interrupt_move)
    if not late_edit:
        with pytest.raises(IsolationError):
            cleanup(ws)
        assert (ws.path / 'seed.txt').read_text() == 'seed\n'
    else:
        assert 'retained workspace' in cleanup(ws)
        files = list((tmp_path / 'wt').glob('retired-worktree-*/worktree/seed.txt'))
        assert len(files) == 1 and files[0].read_text() == 'late writer data\n'


def test_cleanup_cannot_erase_a_late_ignored_file(source, tmp_path, monkeypatch):
    import worktree_lifecycle
    ws = create(source, 'late-ignored', root=tmp_path / 'wt')
    (source / '.git/info/exclude').write_text('late.bin\n')
    original = worktree_lifecycle._git

    def interleave(repo, *args):
        if args[:2] == ('worktree', 'move'):
            (ws.path / 'late.bin').write_bytes(b'important recovery bytes')
        return original(repo, *args)

    monkeypatch.setattr(worktree_lifecycle, '_git', interleave)
    assert 'retained workspace' in cleanup(ws)
    files = list((tmp_path / 'wt').glob('retired-worktree-*/worktree/late.bin'))
    assert len(files) == 1 and files[0].read_bytes() == b'important recovery bytes'


def test_retirement_keeps_open_writer_descriptors_valid(source, tmp_path):
    from worktree_lifecycle import retire_worktree
    ws = create(source, 'open-writer', root=tmp_path / 'wt')
    with (ws.path / 'result.bin').open('wb') as output:
        output.write(b'before ')
        output.flush()
        retired = retire_worktree(source, ws.path)
        output.write(b'after retirement')
    assert (retired.path / 'result.bin').read_bytes() == b'before after retirement'
    assert git(retired.path, 'rev-parse', 'HEAD').strip() == retired.head
    assert git(retired.path, 'symbolic-ref', '-q', 'HEAD') == ''


def test_retirement_rejects_head_advance_without_deleting_either_generation(source, tmp_path, monkeypatch):
    import worktree_lifecycle
    ws = create(source, 'late-commit', root=tmp_path / 'wt')
    original = worktree_lifecycle._git
    def interleave(repo, *args):
        if args[:2] == ('worktree', 'move'):
            (ws.path / 'later.txt').write_text('later committed work\n')
            git(ws.path, 'add', 'later.txt')
            git(ws.path, 'commit', '-qm', 'concurrent generation')
        return original(repo, *args)
    monkeypatch.setattr(worktree_lifecycle, '_git', interleave)
    with pytest.raises(RuntimeError, match='inspect'):
        worktree_lifecycle.retire_worktree(source, ws.path)
    files = list((tmp_path / 'wt').glob('retired-worktree-*/worktree/later.txt'))
    assert len(files) == 1 and files[0].read_text() == 'later committed work\n'
    assert git(source, 'show', 'agent/late-commit:later.txt') == 'later committed work\n'


def test_retirement_allocation_failure_keeps_original_workspace(source, tmp_path, monkeypatch):
    import worktree_lifecycle
    ws = create(source, 'no-space', root=tmp_path / 'wt')
    head = git(ws.path, 'rev-parse', 'HEAD')
    def full_disk(**kwargs):
        raise OSError('fixture disk full')
    monkeypatch.setattr(worktree_lifecycle.tempfile, 'mkdtemp', full_disk)
    with pytest.raises(RuntimeError, match='original workspace retained'):
        worktree_lifecycle.retire_worktree(source, ws.path)
    assert (ws.path / 'seed.txt').read_text() == 'seed\n'
    assert git(ws.path, 'rev-parse', 'HEAD') == head
