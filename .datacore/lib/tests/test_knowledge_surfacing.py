"""Tests for knowledge_surfacing.py dynamic space discovery."""

import os
from pathlib import Path
from datetime import datetime, timedelta

import pytest

# Import the class under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from knowledge_surfacing import KnowledgeSurfacing


def make_space(root: Path, name: str, subdirs: list[str] = None):
    """Helper: create a space directory with optional subdirectories."""
    space = root / name
    space.mkdir(parents=True, exist_ok=True)
    for sub in (subdirs or []):
        (space / sub).mkdir(parents=True, exist_ok=True)
    return space


def make_file(path: Path, content: str = "# Test\nSome content", age_days: int = 0):
    """Create a file with optional age in days."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if age_days > 0:
        import time
        mtime = time.time() - (age_days * 86400)
        os.utime(path, (mtime, mtime))


class TestKnowledgeRootsDiscovery:
    """Test that KnowledgeSurfacing discovers knowledge roots from all spaces."""

    def test_discovers_multiple_knowledge_roots(self, tmp_path):
        """All spaces with 3-knowledge/ are discovered."""
        make_space(tmp_path, "0-personal", ["3-knowledge/zettel"])
        make_space(tmp_path, "1-teamspace", ["3-knowledge/literature"])
        # State dir needed
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        ks = KnowledgeSurfacing(tmp_path)
        assert len(ks.knowledge_roots) == 2
        assert any("0-personal" in str(r) for r in ks.knowledge_roots)
        assert any("1-teamspace" in str(r) for r in ks.knowledge_roots)

    def test_backwards_compatible_single_root(self, tmp_path):
        """knowledge_root property returns first (usually 0-personal)."""
        make_space(tmp_path, "0-personal", ["3-knowledge"])
        make_space(tmp_path, "1-teamspace", ["3-knowledge"])
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        ks = KnowledgeSurfacing(tmp_path)
        assert "0-personal" in str(ks.knowledge_root)

    def test_no_spaces_falls_back(self, tmp_path):
        """Falls back to 0-personal/3-knowledge when no spaces found."""
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        ks = KnowledgeSurfacing(tmp_path)
        assert ks.knowledge_roots == []
        assert "0-personal" in str(ks.knowledge_root)

    def test_skips_non_space_directories(self, tmp_path):
        """Directories not matching [0-9]-* are skipped."""
        make_space(tmp_path, "0-personal", ["3-knowledge"])
        make_space(tmp_path, "docs", ["3-knowledge"])  # Not a space
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        ks = KnowledgeSurfacing(tmp_path)
        assert len(ks.knowledge_roots) == 1


class TestPermissionErrors:
    """Test graceful handling of PermissionError in discovery."""

    def test_permission_error_returns_empty_roots(self, tmp_path):
        """PermissionError during iterdir() results in empty knowledge_roots."""
        (tmp_path / ".datacore" / "state").mkdir(parents=True)
        # Create a non-readable directory to trigger PermissionError
        restricted = tmp_path / "0-personal"
        restricted.mkdir()
        (restricted / "3-knowledge").mkdir()

        # The guard in __init__ wraps iterdir() with try/except
        # Test with a mock that raises
        from unittest.mock import patch
        original_iterdir = Path.iterdir

        def raising_iterdir(self_path):
            if self_path == tmp_path:
                raise PermissionError("access denied")
            return original_iterdir(self_path)

        with patch.object(Path, 'iterdir', raising_iterdir):
            ks = KnowledgeSurfacing(tmp_path)
        assert ks.knowledge_roots == []
        # Should fall back to default
        assert "0-personal" in str(ks.knowledge_root)

    def test_os_error_returns_empty_roots(self, tmp_path):
        """OSError during iterdir() results in empty knowledge_roots."""
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        from unittest.mock import patch
        original_iterdir = Path.iterdir

        def raising_iterdir(self_path):
            if self_path == tmp_path:
                raise OSError("disk error")
            return original_iterdir(self_path)

        with patch.object(Path, 'iterdir', raising_iterdir):
            ks = KnowledgeSurfacing(tmp_path)
        assert ks.knowledge_roots == []


class TestGetRecentFiles:
    """Test that recent files are found across all spaces."""

    def test_finds_files_across_spaces(self, tmp_path):
        """Recent files from multiple spaces are discovered."""
        make_space(tmp_path, "0-personal", ["3-knowledge/zettel"])
        make_space(tmp_path, "1-teamspace", ["3-knowledge/literature"])
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        make_file(tmp_path / "0-personal" / "3-knowledge" / "zettel" / "note1.md")
        make_file(tmp_path / "1-teamspace" / "3-knowledge" / "literature" / "paper1.md")

        ks = KnowledgeSurfacing(tmp_path)
        recent = ks._get_recent_files()
        filenames = [f.name for f, _ in recent]
        assert "note1.md" in filenames
        assert "paper1.md" in filenames

    def test_excludes_old_files(self, tmp_path):
        """Files older than rotation window are excluded."""
        make_space(tmp_path, "0-personal", ["3-knowledge/zettel"])
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        make_file(tmp_path / "0-personal" / "3-knowledge" / "zettel" / "old.md", age_days=60)
        make_file(tmp_path / "0-personal" / "3-knowledge" / "zettel" / "new.md", age_days=1)

        ks = KnowledgeSurfacing(tmp_path)
        recent = ks._get_recent_files()
        filenames = [f.name for f, _ in recent]
        assert "new.md" in filenames
        assert "old.md" not in filenames

    def test_excludes_index_and_readme(self, tmp_path):
        """INDEX* and README.md files are excluded."""
        make_space(tmp_path, "0-personal", ["3-knowledge/zettel"])
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        make_file(tmp_path / "0-personal" / "3-knowledge" / "zettel" / "INDEX.md")
        make_file(tmp_path / "0-personal" / "3-knowledge" / "zettel" / "README.md")
        make_file(tmp_path / "0-personal" / "3-knowledge" / "zettel" / "good-note.md")

        ks = KnowledgeSurfacing(tmp_path)
        recent = ks._get_recent_files()
        filenames = [f.name for f, _ in recent]
        assert "good-note.md" in filenames
        assert "INDEX.md" not in filenames
        assert "README.md" not in filenames


class TestStateManagement:
    """Test state load/save round-trip."""

    def test_default_state(self, tmp_path):
        """Default state has expected structure."""
        make_space(tmp_path, "0-personal", ["3-knowledge"])
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        ks = KnowledgeSurfacing(tmp_path)
        assert 'items' in ks.state
        assert 'config' in ks.state
        assert ks.state['config']['rotation_window_days'] == 30

    def test_state_round_trip(self, tmp_path):
        """State survives save/load cycle."""
        make_space(tmp_path, "0-personal", ["3-knowledge"])
        (tmp_path / ".datacore" / "state").mkdir(parents=True)

        ks = KnowledgeSurfacing(tmp_path)
        ks.state['items']['/test/file.md'] = {
            'last_surfaced': '2026-01-01',
            'surface_count': 3,
        }
        ks._store.save(ks.state)

        ks2 = KnowledgeSurfacing(tmp_path)
        assert '/test/file.md' in ks2.state['items']
        assert ks2.state['items']['/test/file.md']['surface_count'] == 3
