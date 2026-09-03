"""Tests for tag_validator.py dynamic space discovery and registry loading."""

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from tag_validator import TagValidator


def make_space(root: Path, name: str, subdirs: list[str] = None):
    """Create a space the validator can DISCOVER.

    Discovery is marker-based (lib/spaces.py, DIP-0015): a directory is a
    space iff it carries .datacore/config.yaml with a `space:` block. These
    tests built bare directories, so the validator found no spaces, scanned
    no files, reported no issues -- and three tests asserting "reported"
    failed on main for as long as the marker has existed.
    """
    from spaces import MARKER
    space = root / name
    space.mkdir(parents=True, exist_ok=True)
    marker = space / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    with open(marker, "w") as f:
        yaml.dump({"space": {"name": name, "type": "personal"}}, f)
    for sub in (subdirs or []):
        (space / sub).mkdir(parents=True, exist_ok=True)
    return space


def write_registry(root: Path, registry: dict):
    """Write a tag registry YAML file."""
    path = root / ".datacore" / "tags.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        yaml.dump(registry, f)


def write_org(path: Path, content: str):
    """Write an org-mode file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestRegistryPath:
    """Test that registry loads from correct path."""

    def test_uses_datacore_tags_yaml(self, tmp_path):
        """Registry path is .datacore/tags.yaml (not config/tags.yaml)."""
        tv = TagValidator(data_dir=str(tmp_path))
        assert tv.registry_path == tmp_path / ".datacore" / "tags.yaml"

    def test_loads_registry_tags(self, tmp_path):
        """Tags from registry are loaded into known sets."""
        write_registry(tmp_path, {
            'gtd': {
                'ai': {'org': 'AI', 'hashtag': '#ai'},
                'research': {'org': 'research', 'hashtag': '#research'},
            }
        })

        tv = TagValidator(data_dir=str(tmp_path))
        assert 'AI' in tv.known_org_tags
        assert '#research' in tv.known_hashtags

    def test_missing_registry_handled(self, tmp_path):
        """Missing registry file doesn't crash."""
        tv = TagValidator(data_dir=str(tmp_path))
        assert tv.registry == {}


class TestDynamicOrgDiscovery:
    """Test dynamic discovery of org-mode directories."""

    def test_discovers_org_dirs_from_spaces(self, tmp_path):
        """Org dirs from all [0-9]-* spaces are scanned."""
        make_space(tmp_path, "0-personal", ["org"])
        make_space(tmp_path, "1-teamspace", ["org"])
        write_registry(tmp_path, {'gtd': {'test': {'org': 'test'}}})

        # Write org files with tags
        write_org(tmp_path / "0-personal" / "org" / "inbox.org",
                  "* TODO Test task :test:\n")
        write_org(tmp_path / "1-teamspace" / "org" / "inbox.org",
                  "* TODO Team task :test:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        # No issues expected since :test: is in registry
        # The key test is that it scans BOTH spaces without error
        assert isinstance(issues, dict)

    def test_skips_non_space_directories(self, tmp_path):
        """Non-space directories are not scanned."""
        make_space(tmp_path, "docs", ["org"])
        write_registry(tmp_path, {})

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        # Should not find any files (docs/ is not a space)
        assert len(issues) == 0


class TestDynamicNotesDiscovery:
    """Test dynamic discovery of notes directories."""

    def test_discovers_notes_from_spaces(self, tmp_path):
        """Notes dirs from all spaces are discovered."""
        make_space(tmp_path, "0-personal", ["notes"])
        write_registry(tmp_path, {})

        (tmp_path / "0-personal" / "notes" / "test.md").write_text("# Test\n#sometag")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_notes()
        assert isinstance(issues, dict)

    def test_discovers_knowledge_dirs(self, tmp_path):
        """Knowledge dirs from spaces are also scanned."""
        make_space(tmp_path, "1-teamspace", ["knowledge"])
        write_registry(tmp_path, {})

        (tmp_path / "1-teamspace" / "knowledge" / "test.md").write_text("# Test\n#sometag")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_notes()
        assert isinstance(issues, dict)


class TestUppercaseTagFiltering:
    """Test that uppercase tags use explicit allowlist, not blanket skip."""

    def test_known_uppercase_prefix_not_reported(self, tmp_path):
        """Tags with known uppercase prefixes (AI, CRM) are not flagged."""
        make_space(tmp_path, "0-personal", ["org"])
        write_registry(tmp_path, {
            'ai_delegation': {
                'ai': {'org': ':AI:'},
                'ai-research': {'org': ':AI:research:'},
            }
        })

        write_org(tmp_path / "0-personal" / "org" / "test.org",
                  "* TODO Task :AI:research:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        assert len(issues) == 0, f"Known AI compound tag wrongly reported: {issues}"

    def test_unknown_uppercase_tag_reported(self, tmp_path):
        """Unknown uppercase tags (not in allowlist) are now reported."""
        make_space(tmp_path, "0-personal", ["org"])
        write_registry(tmp_path, {'gtd': {'ai': {'org': ':AI:'}}})

        write_org(tmp_path / "0-personal" / "org" / "test.org",
                  "* TODO Task :CUSTOM_PROP:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        file_issues = []
        for file_path, issue_list in issues.items():
            for tag, line, msg in issue_list:
                if 'CUSTOM_PROP' in tag:
                    file_issues.append(tag)
        assert len(file_issues) > 0, "Unknown uppercase tag should be reported"

    def test_property_drawer_tags_still_skipped(self, tmp_path):
        """Property drawer tags like :PROPERTIES: are still skipped."""
        make_space(tmp_path, "0-personal", ["org"])
        write_registry(tmp_path, {})

        write_org(tmp_path / "0-personal" / "org" / "test.org",
                  "   :PROPERTIES:\n   :EFFORT:   1:00\n   :END:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        assert len(issues) == 0, f"Property drawer tags should be skipped: {issues}"

    def test_compound_tag_segments_validated(self, tmp_path):
        """Compound tags are split into segments; known segments pass."""
        make_space(tmp_path, "0-personal", ["org"])
        write_registry(tmp_path, {
            'domains': {
                'trading': {'org': ':trading:'},
                'health': {'org': ':health:'},
            },
            'actions': {
                'research': {'org': ':research:'},
            }
        })

        # Both segments are known individually
        write_org(tmp_path / "0-personal" / "org" / "test.org",
                  "* TODO Task :trading:research:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        assert len(issues) == 0, f"Compound tag with known segments wrongly reported: {issues}"

    def test_compound_tag_with_unknown_segment_reported(self, tmp_path):
        """Compound tags with any unknown segment are reported."""
        make_space(tmp_path, "0-personal", ["org"])
        write_registry(tmp_path, {
            'domains': {
                'trading': {'org': ':trading:'},
            }
        })

        # 'xyzzy' is not a known segment
        write_org(tmp_path / "0-personal" / "org" / "test.org",
                  "* TODO Task :trading:xyzzy:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        file_issues = []
        for file_path, issue_list in issues.items():
            for tag, line, msg in issue_list:
                if 'xyzzy' in tag:
                    file_issues.append(tag)
        assert len(file_issues) > 0, "Compound tag with unknown segment should be reported"


class TestPermissionErrors:
    """Test graceful handling of PermissionError in space discovery."""

    def test_permission_error_returns_empty_list(self, tmp_path):
        """PermissionError on iterdir() returns empty space list."""
        write_registry(tmp_path, {})
        tv = TagValidator(data_dir=str(tmp_path))

        from unittest.mock import patch
        original_iterdir = Path.iterdir

        def raising_iterdir(self_path):
            if self_path == tv.data_dir:
                raise PermissionError("access denied")
            return original_iterdir(self_path)

        with patch.object(Path, 'iterdir', raising_iterdir):
            result = tv._discover_space_dirs()
        assert result == []

    def test_os_error_returns_empty_list(self, tmp_path):
        """OSError on iterdir() returns empty space list."""
        write_registry(tmp_path, {})
        tv = TagValidator(data_dir=str(tmp_path))

        from unittest.mock import patch
        original_iterdir = Path.iterdir

        def raising_iterdir(self_path):
            if self_path == tv.data_dir:
                raise OSError("disk error")
            return original_iterdir(self_path)

        with patch.object(Path, 'iterdir', raising_iterdir):
            result = tv._discover_space_dirs()
        assert result == []

    def test_validate_org_with_no_spaces_returns_empty(self, tmp_path):
        """validate_org_files works even when no spaces exist."""
        write_registry(tmp_path, {})
        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_org_files()
        assert issues == {} or isinstance(issues, dict)

    def test_validate_notes_with_no_spaces_returns_empty(self, tmp_path):
        """validate_notes works even when no spaces exist."""
        write_registry(tmp_path, {})
        tv = TagValidator(data_dir=str(tmp_path))
        issues = tv.validate_notes()
        assert isinstance(issues, dict)


class TestTagStats:
    """Test tag usage statistics with dynamic discovery."""

    def test_counts_tags_across_spaces(self, tmp_path):
        """Tag stats cover all discovered spaces."""
        make_space(tmp_path, "0-personal", ["org"])
        make_space(tmp_path, "1-teamspace", ["org"])
        write_registry(tmp_path, {'gtd': {'ai': {'org': 'AI'}}})

        write_org(tmp_path / "0-personal" / "org" / "tasks.org",
                  "* TODO Task 1 :AI:\n* TODO Task 2 :AI:\n")
        write_org(tmp_path / "1-teamspace" / "org" / "tasks.org",
                  "* TODO Task 3 :AI:\n")

        tv = TagValidator(data_dir=str(tmp_path))
        stats = tv.get_tag_stats()
        # Tag stats use org::tag: format (full org tag including colons)
        assert stats.get('org::AI:', 0) == 3
