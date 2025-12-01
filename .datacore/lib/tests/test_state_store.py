"""Tests for state_store.py - Shared YAML state file utility."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from state_store import YamlStateStore


class TestLoadDefault:
    """Test load() returns default when file is missing."""

    def test_load_returns_default_dict_for_missing_file(self, tmp_path):
        """Missing file returns a copy of the default dict."""
        store = YamlStateStore("state/test.yaml", default={"key": "val"}, data_root=tmp_path)
        result = store.load()
        assert result == {"key": "val"}
        # Ensure it's a copy, not the same object
        result["key"] = "changed"
        assert store.load() == {"key": "val"}

    def test_load_returns_default_empty_dict(self, tmp_path):
        """No explicit default returns empty dict."""
        store = YamlStateStore("state/test.yaml", data_root=tmp_path)
        assert store.load() == {}

    def test_load_returns_default_list(self, tmp_path):
        """Non-dict defaults like lists are returned correctly."""
        store = YamlStateStore("state/test.yaml", default=[], data_root=tmp_path)
        assert store.load() == []

    def test_load_returns_default_for_empty_yaml(self, tmp_path):
        """Empty YAML file (None after parse) returns default."""
        f = tmp_path / "state" / "test.yaml"
        f.parent.mkdir(parents=True)
        f.write_text("")
        store = YamlStateStore("state/test.yaml", default={"sessions": []}, data_root=tmp_path)
        assert store.load() == {"sessions": []}


class TestSaveAndLoad:
    """Test save/load round-trip."""

    def test_save_and_load_roundtrip(self, tmp_path):
        """Data survives a save/load cycle."""
        store = YamlStateStore("state/test.yaml", data_root=tmp_path)
        data = {"count": 42, "items": ["a", "b"]}
        store.save(data)
        assert store.load() == data

    def test_save_creates_parent_dirs(self, tmp_path):
        """save() creates intermediate directories."""
        store = YamlStateStore("deep/nested/dir/test.yaml", data_root=tmp_path)
        store.save({"ok": True})
        assert store.path.exists()
        assert store.load() == {"ok": True}

    def test_save_overwrites_existing(self, tmp_path):
        """save() overwrites previous content."""
        store = YamlStateStore("state/test.yaml", data_root=tmp_path)
        store.save({"version": 1})
        store.save({"version": 2})
        assert store.load() == {"version": 2}


class TestAppendToList:
    """Test append_to_list functionality."""

    def test_append_to_list_creates_file(self, tmp_path):
        """append_to_list creates file when it doesn't exist."""
        store = YamlStateStore("state/queue.yaml", default=[], data_root=tmp_path)
        store.append_to_list([{"id": 1}, {"id": 2}])
        result = store.load()
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_append_to_list_extends_existing(self, tmp_path):
        """append_to_list extends an existing list."""
        store = YamlStateStore("state/queue.yaml", default=[], data_root=tmp_path)
        store.save([{"id": 1}])
        store.append_to_list([{"id": 2}, {"id": 3}])
        result = store.load()
        assert len(result) == 3

    def test_append_with_max_size(self, tmp_path):
        """append_to_list caps the list at max_size, keeping latest entries."""
        store = YamlStateStore("state/queue.yaml", default=[], data_root=tmp_path)
        store.save([{"id": i} for i in range(5)])
        store.append_to_list([{"id": 5}, {"id": 6}], max_size=3)
        result = store.load()
        assert len(result) == 3
        assert result[0]["id"] == 4
        assert result[1]["id"] == 5
        assert result[2]["id"] == 6

    def test_append_to_list_handles_corrupt_file(self, tmp_path):
        """append_to_list recovers from corrupt YAML."""
        f = tmp_path / "state" / "queue.yaml"
        f.parent.mkdir(parents=True)
        f.write_text("not: a: valid: yaml: list: {{{{")
        store = YamlStateStore("state/queue.yaml", default=[], data_root=tmp_path)
        store.append_to_list([{"id": 1}])
        result = store.load()
        assert len(result) == 1

    def test_append_to_list_handles_non_list_content(self, tmp_path):
        """append_to_list starts fresh if file contains a dict instead of list."""
        store = YamlStateStore("state/queue.yaml", default=[], data_root=tmp_path)
        store.save({"not": "a list"})
        store.append_to_list([{"id": 1}])
        result = store.load()
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_append_max_size_zero_means_unlimited(self, tmp_path):
        """max_size=0 (default) means no cap."""
        store = YamlStateStore("state/queue.yaml", default=[], data_root=tmp_path)
        store.save([{"id": i} for i in range(100)])
        store.append_to_list([{"id": 100}], max_size=0)
        result = store.load()
        assert len(result) == 101
