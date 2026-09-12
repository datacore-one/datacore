"""Regression tests for the 50 MB file-size guard in git_fleet_sync.

Issue #29, item 3: the sync script must refuse to commit files ≥50 MB.
Motivation: knowledge.db hit GitHub's 100 MB hard push limit and blocked ALL
pushes of the repo (observed 2026-06-11). The guard leaves headroom before
that limit is reached and tells the operator to add the file to .gitignore.
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


def _init_repo(path: Path) -> None:
    subprocess.run(['git', 'init', '-q', '--initial-branch=main'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=path, check=True)
    (path / 'seed.txt').write_text('hello\n')
    subprocess.run(['git', 'add', 'seed.txt'], cwd=path, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=path, check=True)


# ── structural: pin the guard to the source ─────────────────────────────────

def test_source_has_size_guard():
    src = (LIB / "git_fleet_sync.py").read_text()
    assert "50" in src, "50 MB threshold missing from git_fleet_sync.py"
    assert "oversized" in src, "oversized skip label missing from git_fleet_sync.py"
    assert ".gitignore" in src or "gitignore" in src, "gitignore hint missing"


def test_size_guard_precedes_git_add():
    src = (LIB / "git_fleet_sync.py").read_text()
    loop_start = src.index("for line in porcelain.splitlines():")
    size_guard_pos = src.index("oversized", loop_start)
    git_add_pos = src.index("git', 'add'", loop_start)
    assert size_guard_pos < git_add_pos, (
        "size guard must appear before 'git add' in the staging loop"
    )


# ── functional ───────────────────────────────────────────────────────────────

def test_oversized_file_is_skipped(tmp_path):
    """A file ≥50 MB must land in skipped, not committed."""
    _init_repo(tmp_path)
    big = tmp_path / 'large.db'
    big.write_bytes(b'\x00' * (50 * 1024 * 1024))  # exactly 50 MB

    result = fs.sync_repo(tmp_path, execute=False)

    skipped_paths = [p for p, _ in result['skipped']]
    assert 'large.db' in skipped_paths, (
        f"50 MB file should be in skipped; got committed={result['committed']}, "
        f"skipped={result['skipped']}"
    )
    assert 'large.db' not in result['committed']


def test_oversized_skip_reason_mentions_gitignore(tmp_path):
    """The skip message must tell the operator what to do."""
    _init_repo(tmp_path)
    big = tmp_path / 'large.db'
    big.write_bytes(b'\x00' * (51 * 1024 * 1024))

    result = fs.sync_repo(tmp_path, execute=False)

    reasons = {p: r for p, r in result['skipped']}
    assert 'large.db' in reasons
    assert 'gitignore' in reasons['large.db'].lower(), (
        f"skip reason should mention .gitignore; got: {reasons['large.db']!r}"
    )


def test_normal_file_under_limit_is_committed(tmp_path):
    """A small file must not be caught by the size guard."""
    _init_repo(tmp_path)
    (tmp_path / 'small.txt').write_text('just a note\n')

    result = fs.sync_repo(tmp_path, execute=False)

    assert 'small.txt' in result['committed'], (
        f"small file should not be blocked by size guard; skipped={result['skipped']}"
    )
