"""Regression tests for the merge/rebase-in-progress guards in git_fleet_sync.

Issue #28: a background sync fired during a hand-resolution and pushed literal
Git conflict markers to origin/main of a shared repo.

Two guards prevent this:
  1. MERGE_HEAD / rebase-merge / rebase-apply presence → skip the whole repo
  2. Porcelain XY code containing 'U' (unmerged file) → skip that file
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import git_fleet_sync as fs  # noqa: E402


# ── structural: pin the guards to the source ────────────────────────────────

def test_source_has_merge_head_guard():
    src = (LIB / "git_fleet_sync.py").read_text()
    assert "MERGE_HEAD" in src, "MERGE_HEAD guard missing from git_fleet_sync.py"
    assert "rebase-merge" in src, "rebase-merge guard missing from git_fleet_sync.py"
    assert "rebase-apply" in src, "rebase-apply guard missing from git_fleet_sync.py"


def test_source_has_unmerged_file_guard():
    src = (LIB / "git_fleet_sync.py").read_text()
    assert "change.unmerged" in src, "complete unmerged-index guard missing from git_fleet_sync.py"
    assert "MERGE CONFLICT" in src, "MERGE CONFLICT skip label missing from git_fleet_sync.py"


# ── functional: actual temp git repos ──────────────────────────────────────

def _init_repo(path: Path) -> None:
    """Create a minimal git repo with one commit on main."""
    subprocess.run(['git', 'init', '-q', '--initial-branch=main'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=path, check=True)
    (path / 'file.txt').write_text('hello\n')
    subprocess.run(['git', 'add', 'file.txt'], cwd=path, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=path, check=True)


def test_merge_head_present_skips_repo(tmp_path):
    """sync_repo must not stage or commit when MERGE_HEAD exists."""
    _init_repo(tmp_path)
    (tmp_path / '.git' / 'MERGE_HEAD').write_text('deadbeefdeadbeefdeadbeefdeadbeef00000000\n')
    (tmp_path / 'file.txt').write_text('modified\n')  # dirty working tree

    result = fs.sync_repo(tmp_path, execute=False)

    assert 'merge' in result['status'].lower() or 'rebase' in result['status'].lower(), (
        f"expected merge/rebase skip, got: {result['status']!r}"
    )
    assert result['committed'] == [], "no files should be staged when MERGE_HEAD is present"


def test_rebase_merge_dir_skips_repo(tmp_path):
    """sync_repo must not stage or commit when rebase-merge directory exists."""
    _init_repo(tmp_path)
    (tmp_path / '.git' / 'rebase-merge').mkdir()
    (tmp_path / 'file.txt').write_text('modified\n')

    result = fs.sync_repo(tmp_path, execute=False)

    assert 'merge' in result['status'].lower() or 'rebase' in result['status'].lower(), (
        f"expected rebase skip, got: {result['status']!r}"
    )
    assert result['committed'] == []


def test_unmerged_file_is_skipped_not_committed(tmp_path, monkeypatch):
    """A file with a U in its porcelain XY status must land in skipped, not committed."""
    _init_repo(tmp_path)
    monkeypatch.setattr(fs, 'review_gate', lambda *args: '')
    def git(*args, check=True):
        return subprocess.run(['git', *args], cwd=tmp_path, check=check, capture_output=True)
    git('checkout', '-b', 'other')
    (tmp_path / 'file.txt').write_text('other\n')
    git('commit', '-am', 'other')
    git('checkout', 'main')
    (tmp_path / 'file.txt').write_text('main\n')
    git('commit', '-am', 'main')
    assert git('merge', 'other', check=False).returncode != 0
    # Exercise the secondary guard even if merge metadata is missing. The
    # actual unmerged index stages and conflict-marked file remain intact.
    (tmp_path / '.git' / 'MERGE_HEAD').unlink()
    before = git('ls-files', '--stage', '-z').stdout
    contents = (tmp_path / 'file.txt').read_bytes()
    result = fs.sync_repo(tmp_path, execute=True)
    assert result['committed'] == []
    assert any(path == 'file.txt' and 'MERGE CONFLICT' in reason
               for path, reason in result['skipped'])
    assert git('ls-files', '--stage', '-z').stdout == before
    assert (tmp_path / 'file.txt').read_bytes() == contents
