"""Tests for zettel_db.py dynamic space discovery."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from zettel_db import _discover_spaces


def make_space(root: Path, name: str, subdirs: list[str] = None):
    """Helper: create a space directory with optional subdirectories."""
    space = root / name
    space.mkdir(parents=True)
    for sub in (subdirs or []):
        (space / sub).mkdir(parents=True)
    return space


class TestDiscoverSpaces:
    """Test _discover_spaces() dynamic space discovery."""

    def test_discovers_numbered_directories(self, tmp_path):
        """Spaces matching [0-9]-* pattern are discovered."""
        make_space(tmp_path, "0-personal", ["org", "journal", "3-knowledge/zettel"])
        make_space(tmp_path, "1-teamspace", ["org", "journal"])
        make_space(tmp_path, "docs")  # Not a space

        spaces = _discover_spaces(data_root=tmp_path)
        assert "personal" in spaces
        assert "teamspace" in spaces
        assert "docs" not in spaces

    def test_space_name_strips_number_prefix(self, tmp_path):
        """Space key is the name without the leading N- prefix."""
        make_space(tmp_path, "0-personal")
        make_space(tmp_path, "3-fds")

        spaces = _discover_spaces(data_root=tmp_path)
        assert "personal" in spaces
        assert "fds" in spaces

    def test_scan_paths_only_include_existing_dirs(self, tmp_path):
        """Only existing subdirectories are added to scan_paths."""
        make_space(tmp_path, "0-personal", [
            "3-knowledge/zettel",
            "3-knowledge/pages",
            "org",
            "journal",
        ])

        spaces = _discover_spaces(data_root=tmp_path)
        scan_paths = spaces["personal"]["scan_paths"]
        scan_names = [p.name for p in scan_paths]
        assert "zettel" in scan_names
        assert "pages" in scan_names
        assert "clippings" not in scan_names

    def test_org_paths_only_if_org_dir_exists(self, tmp_path):
        """org_paths is empty if space has no org/ directory."""
        make_space(tmp_path, "0-personal")

        spaces = _discover_spaces(data_root=tmp_path)
        assert spaces["personal"]["org_paths"] == []

    def test_journal_path_prefers_journal_over_notes(self, tmp_path):
        """journal/ is preferred over notes/journals/ when both exist."""
        make_space(tmp_path, "0-personal", ["journal", "notes/journals"])

        spaces = _discover_spaces(data_root=tmp_path)
        assert spaces["personal"]["journal_path"].name == "journal"

    def test_journal_path_falls_back_to_notes_journals(self, tmp_path):
        """Falls back to notes/journals/ when journal/ doesn't exist."""
        make_space(tmp_path, "0-personal", ["notes/journals"])

        spaces = _discover_spaces(data_root=tmp_path)
        assert str(spaces["personal"]["journal_path"]).endswith("notes/journals")

    def test_empty_root_returns_empty_dict(self, tmp_path):
        """Empty DATA_ROOT returns empty spaces dict."""
        spaces = _discover_spaces(data_root=tmp_path)
        assert spaces == {}

    def test_nonexistent_root_returns_empty_dict(self):
        """Non-existent DATA_ROOT returns empty spaces dict."""
        spaces = _discover_spaces(data_root=Path("/nonexistent/path"))
        assert spaces == {}

    def test_spaces_sorted_by_directory_name(self, tmp_path):
        """Spaces are discovered in sorted directory order."""
        make_space(tmp_path, "2-projectspace")
        make_space(tmp_path, "0-personal")
        make_space(tmp_path, "1-teamspace")

        spaces = _discover_spaces(data_root=tmp_path)
        keys = list(spaces.keys())
        assert keys == ["personal", "teamspace", "projectspace"]

    def test_digit_only_directory_uses_full_name(self, tmp_path):
        """Directory like '5' (no dash) uses the full name as key."""
        make_space(tmp_path, "5")

        spaces = _discover_spaces(data_root=tmp_path)
        assert "5" in spaces

    def test_permission_error_returns_empty_dict(self, tmp_path):
        """PermissionError on iterdir() returns empty dict gracefully."""
        make_space(tmp_path, "0-personal")
        original_iterdir = Path.iterdir

        def raising_iterdir(self_path):
            if self_path == tmp_path:
                raise PermissionError("denied")
            return original_iterdir(self_path)

        with patch.object(Path, 'iterdir', raising_iterdir):
            spaces = _discover_spaces(data_root=tmp_path)
        assert spaces == {}

    def test_os_error_returns_empty_dict(self, tmp_path):
        """OSError on iterdir() returns empty dict gracefully."""
        make_space(tmp_path, "0-personal")
        original_iterdir = Path.iterdir

        def raising_iterdir(self_path):
            if self_path == tmp_path:
                raise OSError("disk error")
            return original_iterdir(self_path)

        with patch.object(Path, 'iterdir', raising_iterdir):
            spaces = _discover_spaces(data_root=tmp_path)
        assert spaces == {}
