"""Tests for settings/config loading from sync engine."""

import os
import pytest
import yaml
from pathlib import Path

from sync.engine import SyncEngine


@pytest.fixture
def data_dir(tmp_path):
    """Create a temporary data directory with .datacore/ subdirectory."""
    dc = tmp_path / ".datacore"
    dc.mkdir()
    return tmp_path


def _write_yaml(path, filename, data):
    (path / filename).write_text(yaml.dump(data))


class TestDeepMerge:
    """Tests for SyncEngine._deep_merge()."""

    def test_basic_merge(self, data_dir):
        """Simple key override works."""
        engine = SyncEngine(data_dir)
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        engine._deep_merge(base, override)
        assert base == {"a": 1, "b": 3}

    def test_nested_merge(self, data_dir):
        """Nested dicts are merged recursively."""
        engine = SyncEngine(data_dir)
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        engine._deep_merge(base, override)
        assert base == {"outer": {"a": 1, "b": 3, "c": 4}}

    def test_preserves_non_overlapping(self, data_dir):
        """Non-overlapping keys are preserved."""
        engine = SyncEngine(data_dir)
        base = {"x": 1}
        override = {"y": 2}
        engine._deep_merge(base, override)
        assert base == {"x": 1, "y": 2}

    def test_override_replaces_non_dict(self, data_dir):
        """Non-dict values are replaced, not merged."""
        engine = SyncEngine(data_dir)
        base = {"a": [1, 2]}
        override = {"a": [3, 4]}
        engine._deep_merge(base, override)
        assert base == {"a": [3, 4]}


class TestLoadConfig:
    """Tests for SyncEngine.load_config()."""

    def test_loads_base_settings(self, data_dir):
        """Loads settings.yaml as base config."""
        dc = data_dir / ".datacore"
        _write_yaml(dc, "settings.yaml", {
            "sync": {"adapters": {}},
            "editor": {"open_markdown_on_generate": True}
        })
        engine = SyncEngine(data_dir)
        result = engine.load_config()
        assert result is True
        assert engine.config.get("editor", {}).get("open_markdown_on_generate") is True

    def test_merges_local_settings(self, data_dir):
        """Local settings override base settings."""
        dc = data_dir / ".datacore"
        _write_yaml(dc, "settings.yaml", {
            "sync": {"adapters": {}},
            "editor": {"open_markdown_on_generate": True}
        })
        _write_yaml(dc, "settings.local.yaml", {
            "editor": {"open_markdown_on_generate": False}
        })
        engine = SyncEngine(data_dir)
        engine.load_config()
        assert engine.config["editor"]["open_markdown_on_generate"] is False

    def test_missing_files_returns_true(self, data_dir):
        """Returns True even with no settings files (empty config)."""
        engine = SyncEngine(data_dir)
        result = engine.load_config()
        assert result is True
        assert engine.config == {}
