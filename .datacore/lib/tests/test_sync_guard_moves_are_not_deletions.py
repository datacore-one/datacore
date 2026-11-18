"""A file moved to another folder is not a deleted file (sync guard, MEM-61).

Found 2026-11-18. One machine moved 27 old inbox files into the space's
archive folder, as planned. Every other machine's sync then refused the whole
space ("would remove tracked content: deletes 27 tracked files"), because the
guard compared trees without following moves. The space stayed 56 commits
behind on the hub's machine, so nothing delegated there arrived.

The guard exists to stop content being lost. A file whose exact content sits
at a new path is not lost. A real mass deletion still stops the sync (the
promise eval test_promise_MEM_61 covers that and is unchanged).
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from git_fleet_sync import incoming_losses  # noqa: E402
from tests.test_promise_MEM_61_sync_never_deletes_for_a_missing_file import _git, _setup  # noqa: E402


def _losses(work):
    """What the guard says about merging origin's side, after a fetch."""
    _git(work, "fetch", "-q", "origin")
    return incoming_losses(work, "origin/main")


def test_a_mass_move_into_an_archive_folder_syncs(tmp_path):
    origin, data, work, other = _setup(tmp_path)
    (other / "archive").mkdir()
    for i in range(25):
        _git(other, "mv", f"notes/n{i:02d}.md", f"archive/n{i:02d}.md")
    _git(other, "commit", "-q", "-m", "archive the notes")
    _git(other, "push", "-q", "origin", "main")
    assert _losses(work) == [], "a move of 25 files was judged a loss"


def test_a_move_that_also_deletes_many_files_still_stops(tmp_path):
    origin, data, work, other = _setup(tmp_path)
    (other / "archive").mkdir()
    for i in range(10):
        _git(other, "mv", f"notes/n{i:02d}.md", f"archive/n{i:02d}.md")
    for i in range(10, 25):
        (other / "notes" / f"n{i:02d}.md").unlink()
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "archive ten, lose fifteen")
    _git(other, "push", "-q", "origin", "main")
    losses = _losses(work)
    assert losses and "deletes 15 tracked files" in losses[0], losses


def test_a_moved_file_that_was_emptied_on_the_way_counts_as_lost(tmp_path):
    origin, data, work, other = _setup(tmp_path)
    (other / "archive").mkdir()
    for i in range(25):
        (other / "notes" / f"n{i:02d}.md").unlink()
        (other / "archive" / f"n{i:02d}.md").write_text("")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "same names, no content")
    _git(other, "push", "-q", "origin", "main")
    losses = _losses(work)
    assert losses and "deletes 25 tracked files" in losses[0], losses
