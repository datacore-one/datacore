"""media_store.py: each space's media/ folder, outside git (owner, 2026-11-14).

Large files live in <space>/media/ under the path they would have had, a
relative symlink stays at the old place so they are still found where they
belong, and the folder is copied to the storage host with rsync -- never deleting there.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_store  # noqa: E402


def _space(root: Path, name: str = "4-forge") -> Path:
    space = root / name
    space.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(space)], check=True, capture_output=True)
    (space / ".gitignore").write_text("*.key\n")
    return space


def test_move_puts_the_file_in_media_and_leaves_a_relative_symlink(tmp_path):
    space = _space(tmp_path)
    src = space / "content" / "video" / "promo.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00video")
    media_store.move(src)
    dest = space / "media" / "content" / "video" / "promo.mp4"
    assert dest.read_bytes() == b"\x00video"
    assert src.is_symlink()
    assert not os.path.isabs(os.readlink(src))
    assert src.read_bytes() == b"\x00video"


def test_move_makes_git_ignore_the_media_folder(tmp_path):
    space = _space(tmp_path)
    src = space / "content" / "a.mov"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    media_store.move(src)
    r = subprocess.run(["git", "check-ignore", "-q", "media/content/a.mov"], cwd=space)
    assert r.returncode == 0, "media/ is not ignored by git"
    assert "*.key" in (space / ".gitignore").read_text(), "the existing ignore lines were lost"


def test_ignore_is_idempotent(tmp_path):
    space = _space(tmp_path)
    media_store.ensure_ignored(space)
    media_store.ensure_ignored(space)
    assert (space / ".gitignore").read_text().count("/media/") == 1


def test_move_refuses_a_file_outside_a_space(tmp_path):
    stray = tmp_path / "loose.mp4"
    stray.write_bytes(b"x")
    with pytest.raises(media_store.MediaError):
        media_store.move(stray)
    assert stray.is_file() and not stray.is_symlink()


def test_move_never_overwrites_a_file_already_in_media(tmp_path):
    space = _space(tmp_path)
    src = space / "content" / "a.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"new")
    existing = space / "media" / "content" / "a.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")
    with pytest.raises(media_store.MediaError):
        media_store.move(src)
    assert existing.read_bytes() == b"old" and src.read_bytes() == b"new"


def test_backup_copies_media_and_never_deletes_at_the_destination(tmp_path):
    root = tmp_path / "Data"
    space = _space(root)
    f = space / "media" / "content" / "clip.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"clip")
    _space(root, "5-plur")  # no media folder: skipped, not an error
    dest = tmp_path / "storage"
    dest.mkdir()
    assert media_store.backup(root, str(dest), host="mac") == 0
    copy = dest / "mac" / "4-forge" / "content" / "clip.mp4"
    assert copy.read_bytes() == b"clip"
    f.unlink()
    assert media_store.backup(root, str(dest), host="mac") == 0
    assert copy.exists(), "the backup deleted a file at the destination"


def test_backup_without_a_destination_says_so(tmp_path, monkeypatch, capsys):
    """Shipped code names no host: the target comes from the private registry."""
    monkeypatch.delenv("MEDIA_BACKUP_DEST", raising=False)
    monkeypatch.setattr(media_store, "default_dest", lambda: None)
    assert media_store.main(["backup"]) == 2
    assert "roles.media_backup" in capsys.readouterr().err
