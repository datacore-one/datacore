"""Tests for context_merge.py - Layered Context Merge Utility."""

import os
import pytest
from pathlib import Path

from context_merge import (
    merge_context,
    rebuild_context,
    validate_layers,
    find_all_contexts,
    LAYERS,
    PRIVATE_PATTERNS,
)


@pytest.fixture
def tmp_context(tmp_path):
    """Create a temporary directory with layered context files."""
    return tmp_path


def _write(path, name, suffix, content):
    (path / f"{name}.{suffix}.md").write_text(content)


class TestMergeContext:
    """Tests for merge_context()."""

    def test_base_only(self, tmp_context):
        """Merge with only a base layer produces content."""
        _write(tmp_context, "CLAUDE", "base", "# Base Content\n\nHello world")
        content, warnings = merge_context(tmp_context)
        assert "# Base Content" in content
        assert "Hello world" in content
        assert len(warnings) == 0

    def test_base_plus_local(self, tmp_context):
        """Merge with base + local includes both layers."""
        _write(tmp_context, "CLAUDE", "base", "# Base")
        _write(tmp_context, "CLAUDE", "local", "# Local additions")
        content, warnings = merge_context(tmp_context)
        assert "# Base" in content
        assert "# Local additions" in content

    def test_all_layers(self, tmp_context):
        """Merge with all layers includes them in order."""
        _write(tmp_context, "CLAUDE", "base", "BASE")
        _write(tmp_context, "CLAUDE", "space", "SPACE")
        _write(tmp_context, "CLAUDE", "team", "TEAM")
        _write(tmp_context, "CLAUDE", "local", "LOCAL")
        content, warnings = merge_context(tmp_context)
        assert content.index("BASE") < content.index("SPACE")
        assert content.index("SPACE") < content.index("TEAM")
        assert content.index("TEAM") < content.index("LOCAL")

    def test_layer_markers_included(self, tmp_context):
        """Layer boundary markers are included by default."""
        _write(tmp_context, "CLAUDE", "base", "content")
        content, _ = merge_context(tmp_context, include_markers=True)
        assert "Layer: BASE (PUBLIC)" in content

    def test_layer_markers_excluded(self, tmp_context):
        """Layer markers can be excluded."""
        _write(tmp_context, "CLAUDE", "base", "content")
        content, _ = merge_context(tmp_context, include_markers=False)
        assert "Layer:" not in content

    def test_custom_name(self, tmp_context):
        """Merge works with custom file names."""
        _write(tmp_context, "CONFIG", "base", "# Config base")
        content, warnings = merge_context(tmp_context, name="CONFIG")
        assert "# Config base" in content


class TestValidation:
    """Tests for privacy validation."""

    def test_email_in_public_warns(self, tmp_context):
        """Email address in PUBLIC layer triggers warning."""
        _write(tmp_context, "CLAUDE", "base", "Contact: user@example.com")
        _, warnings = merge_context(tmp_context, validate=True)
        assert len(warnings) > 0
        assert any("email" in w.lower() for w in warnings)

    def test_ip_secret_in_public_warns(self, tmp_context):
        """Potential secret in PUBLIC layer triggers warning."""
        _write(tmp_context, "CLAUDE", "base", 'api_key: sk-abc123secret')
        _, warnings = merge_context(tmp_context, validate=True)
        assert len(warnings) > 0
        assert any("secret" in w.lower() for w in warnings)

    def test_email_in_local_no_warning(self, tmp_context):
        """Email in LOCAL layer does not trigger warning."""
        _write(tmp_context, "CLAUDE", "base", "# Clean base")
        _write(tmp_context, "CLAUDE", "local", "Contact: user@example.com")
        _, warnings = merge_context(tmp_context, validate=True)
        assert len(warnings) == 0

    def test_validate_layers_returns_warnings(self, tmp_context):
        """validate_layers() returns list of warnings."""
        _write(tmp_context, "CLAUDE", "base", "email: test@test.com")
        warnings = validate_layers(tmp_context)
        assert len(warnings) > 0


class TestRebuildContext:
    """Tests for rebuild_context()."""

    def test_rebuild_creates_file(self, tmp_context):
        """rebuild_context writes composed file."""
        _write(tmp_context, "CLAUDE", "base", "# Hello")
        success, warnings = rebuild_context(tmp_context)
        output = tmp_context / "CLAUDE.md"
        assert output.exists()
        assert "# Hello" in output.read_text()

    def test_dry_run_no_file(self, tmp_context):
        """dry_run mode doesn't write file."""
        _write(tmp_context, "CLAUDE", "base", "# Hello")
        success, warnings = rebuild_context(tmp_context, dry_run=True)
        output = tmp_context / "CLAUDE.md"
        assert not output.exists()

    def test_no_layers_returns_false(self, tmp_context):
        """Returns False when no layer files exist."""
        success, warnings = rebuild_context(tmp_context)
        assert success is False
        assert len(warnings) > 0


class TestFindAllContexts:
    """Tests for find_all_contexts()."""

    def test_finds_base_files(self, tmp_context):
        """Discovers .base.md files recursively."""
        _write(tmp_context, "CLAUDE", "base", "root")
        sub = tmp_context / "sub"
        sub.mkdir()
        _write(sub, "CLAUDE", "base", "sub")
        contexts = find_all_contexts(tmp_context)
        assert len(contexts) == 2
        paths = [str(p) for p, _ in contexts]
        assert any("sub" in p for p in paths)
