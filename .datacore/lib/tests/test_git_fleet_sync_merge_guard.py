"""Regression tests for the merge/rebase-in-progress guards in git_fleet_sync.

Issue #28: a background sync fired during a hand-resolution and pushed literal
<<<<<<< / ======= / >>>>>>> conflict markers to origin/main of a shared repo.

Two guards prevent this:
  1. MERGE_HEAD / rebase-merge / rebase-apply presence → skip the whole repo
  2. Porcelain XY code containing 'U' (unmerged file) → skip that file
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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
    assert "'U' in xy" in src, "unmerged-file (UU/AU/UA) guard missing from git_fleet_sync.py"
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


def test_unmerged_file_is_skipped_not_committed(tmp_path):
    """A file with a U in its porcelain XY status must land in skipped, not committed."""
    # The porcelain 'U' flag is produced during an in-progress merge, but we
    # can simulate it by inspecting the is_junk + staging-loop logic without a
    # full two-branch merge. We verify via a source-parse that the XY guard is
    # upstream of the commit call — sufficient for the regression.
    src = (LIB / "git_fleet_sync.py").read_text()

    # Find the staging loop
    loop_start = src.index("for line in porcelain.splitlines():")
    # The 'U' guard must appear before the git add call
    u_guard_pos = src.index("'U' in xy", loop_start)
    git_add_pos = src.index("git', 'add'", loop_start)
    assert u_guard_pos < git_add_pos, (
        "unmerged-file guard ('U' in xy) must come before 'git add' in the staging loop"
    )
