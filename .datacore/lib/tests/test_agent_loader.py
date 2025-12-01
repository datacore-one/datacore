"""Tests for agent_loader.py - Agent Context Loader (DIP-0016)."""

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from agent_loader import (
    load_registry,
    get_agent_metadata,
    load_file_content,
    load_dip_content,
    load_agent_context,
    format_context_for_prompt,
)


@pytest.fixture
def registry_dir(tmp_path):
    """Create a minimal registry structure."""
    reg_dir = tmp_path / ".datacore" / "registry"
    reg_dir.mkdir(parents=True)
    return reg_dir


@pytest.fixture
def registry_file(registry_dir):
    """Create a registry with test agents."""
    registry = {
        "agents": {
            "test-agent": {
                "description": "A test agent",
                "reads": {
                    "required": ["test-file.md"],
                    "contextual": ["query: test search"],
                },
                "references": {
                    "dips": ["DIP-0009"],
                    "specs": ["test-spec.md"],
                },
            },
            "no-reads-agent": {
                "description": "Agent with no reads",
            },
        },
        "module_agents": {
            "module-test-agent": {
                "description": "A module agent",
                "module": "test-module",
            },
        },
    }
    path = registry_dir / "agents.yaml"
    with open(path, "w") as f:
        yaml.dump(registry, f)
    return path


class TestLoadRegistry:
    """Test registry loading."""

    def test_missing_registry_returns_empty(self, tmp_path, monkeypatch):
        """Missing registry file returns empty dict."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", tmp_path / "nonexistent.yaml")
        result = load_registry()
        assert result == {"agents": {}, "module_agents": {}}

    def test_loads_valid_registry(self, registry_file, monkeypatch):
        """Valid registry file is loaded correctly."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        result = load_registry()
        assert "agents" in result
        assert "test-agent" in result["agents"]


class TestGetAgentMetadata:
    """Test agent metadata retrieval."""

    def test_finds_core_agent(self, registry_file, monkeypatch):
        """Core agents are found."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        meta = get_agent_metadata("test-agent")
        assert meta is not None
        assert meta["description"] == "A test agent"

    def test_finds_module_agent(self, registry_file, monkeypatch):
        """Module agents are found."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        meta = get_agent_metadata("module-test-agent")
        assert meta is not None
        assert meta["module"] == "test-module"

    def test_missing_agent_returns_none(self, registry_file, monkeypatch):
        """Non-existent agent returns None."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        meta = get_agent_metadata("nonexistent-agent")
        assert meta is None


class TestLoadFileContent:
    """Test file content loading."""

    def test_loads_relative_path(self, tmp_path, monkeypatch):
        """Relative paths resolve from DATACORE_ROOT."""
        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        (tmp_path / "test.md").write_text("# Hello")
        content = load_file_content("test.md")
        assert content == "# Hello"

    def test_loads_absolute_path(self, tmp_path):
        """Absolute paths work directly."""
        f = tmp_path / "abs-test.md"
        f.write_text("absolute content")
        content = load_file_content(str(f))
        assert content == "absolute content"

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        """Missing files return None."""
        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        content = load_file_content("nonexistent.md")
        assert content is None

    def test_loads_directory_md_files(self, tmp_path, monkeypatch):
        """Directories return concatenated .md files."""
        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        d = tmp_path / "docs"
        d.mkdir()
        (d / "a.md").write_text("File A")
        (d / "b.md").write_text("File B")
        (d / "c.txt").write_text("Not included")

        content = load_file_content("docs")
        assert "File A" in content
        assert "File B" in content
        assert "Not included" not in content

    def test_glob_pattern(self, tmp_path, monkeypatch):
        """Glob patterns match multiple files."""
        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        d = tmp_path / "notes"
        d.mkdir()
        (d / "note1.md").write_text("Note 1")
        (d / "note2.md").write_text("Note 2")

        content = load_file_content("notes/*.md")
        assert "Note 1" in content
        assert "Note 2" in content


class TestLoadDipContent:
    """Test DIP content loading."""

    def test_loads_dip_by_reference(self, tmp_path, monkeypatch):
        """DIP files are found by reference."""
        dips_dir = tmp_path / ".datacore" / "dips"
        dips_dir.mkdir(parents=True)
        (dips_dir / "DIP-0009-gtd-specification.md").write_text("# DIP-0009\nGTD spec content")

        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        content = load_dip_content("DIP-0009")
        assert content is not None
        assert "GTD spec content" in content

    def test_missing_dip_returns_none(self, tmp_path, monkeypatch):
        """Missing DIP returns None."""
        dips_dir = tmp_path / ".datacore" / "dips"
        dips_dir.mkdir(parents=True)

        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        content = load_dip_content("DIP-9999")
        assert content is None


class TestLoadAgentContext:
    """Test full agent context loading."""

    def test_missing_agent_returns_error(self, registry_file, monkeypatch):
        """Non-existent agent returns error context."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        ctx = load_agent_context("nonexistent")
        assert "error" in ctx
        assert ctx["required_files"] == {}

    def test_loads_required_files(self, tmp_path, registry_file, monkeypatch):
        """Required files are loaded into context."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        (tmp_path / "test-file.md").write_text("Required content")

        ctx = load_agent_context("test-agent", include_dips=False, include_contextual=False)
        assert "test-file.md" in ctx["required_files"]
        assert ctx["required_files"]["test-file.md"] == "Required content"

    def test_truncates_at_max_size(self, tmp_path, registry_file, monkeypatch):
        """Context is truncated when exceeding max_context_size."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        monkeypatch.setattr("agent_loader.DATACORE_ROOT", tmp_path)
        (tmp_path / "test-file.md").write_text("x" * 10000)

        ctx = load_agent_context("test-agent", include_dips=False, include_contextual=False, max_context_size=100)
        assert ctx["truncated"] is True
        assert ctx["total_size"] <= 200  # some overhead

    def test_agent_with_no_reads(self, registry_file, monkeypatch):
        """Agent with no reads config returns empty context."""
        monkeypatch.setattr("agent_loader.REGISTRY_PATH", registry_file)
        ctx = load_agent_context("no-reads-agent", include_dips=False, include_contextual=False)
        assert ctx["required_files"] == {}
        assert ctx["truncated"] is False


class TestFormatContextForPrompt:
    """Test context formatting for prompt injection."""

    def test_formats_required_files(self):
        """Required files are formatted with headers."""
        ctx = {
            "required_files": {"path/to/file.md": "File content here"},
            "dip_content": {},
            "contextual_results": {},
            "truncated": False,
        }
        output = format_context_for_prompt(ctx)
        assert "Required Files" in output
        assert "path/to/file.md" in output
        assert "File content here" in output

    def test_truncation_note(self):
        """Truncation note appears when context was truncated."""
        ctx = {
            "required_files": {},
            "dip_content": {},
            "contextual_results": {},
            "truncated": True,
        }
        output = format_context_for_prompt(ctx)
        assert "truncated" in output.lower()

    def test_empty_context(self):
        """Empty context produces minimal output."""
        ctx = {
            "required_files": {},
            "dip_content": {},
            "contextual_results": {},
            "truncated": False,
        }
        output = format_context_for_prompt(ctx)
        assert "Pre-Loaded Context" in output
