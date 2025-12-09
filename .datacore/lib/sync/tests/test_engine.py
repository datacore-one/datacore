"""
Tests for SyncEngine.

DIP-0010: Task Sync Architecture
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lib to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sync.engine import SyncEngine
from sync.adapters import TaskChange, ChangeType, ExternalTask, SyncResult


class TestSyncEngineInit:
    """Test SyncEngine initialization."""

    def test_default_data_dir(self):
        """Uses DATA_DIR env or ~/Data."""
        with patch.dict(os.environ, {"DATA_DIR": "/test/data"}):
            engine = SyncEngine()
            assert engine.data_dir == Path("/test/data")

    def test_custom_data_dir(self):
        """Accepts custom data_dir."""
        engine = SyncEngine(data_dir="/custom/path")
        assert engine.data_dir == Path("/custom/path")


class TestSyncEngineConfig:
    """Test configuration loading."""

    def test_load_config_missing_file(self, tmp_path):
        """Handles missing config gracefully."""
        engine = SyncEngine(data_dir=str(tmp_path))
        engine.config_dir = tmp_path / ".datacore"
        engine.config_dir.mkdir()

        result = engine.load_config()
        assert result is True
        assert engine.config == {}

    def test_load_config_with_settings(self, tmp_path):
        """Loads settings.yaml correctly."""
        config_dir = tmp_path / ".datacore"
        config_dir.mkdir()

        settings = config_dir / "settings.yaml"
        settings.write_text("""
sync:
  tasks:
    enabled: true
  adapters:
    github:
      enabled: true
      repos:
        - owner: test
          repo: test-repo
""")

        engine = SyncEngine(data_dir=str(tmp_path))
        engine.config_dir = config_dir
        engine.load_config()

        assert engine.config["sync"]["tasks"]["enabled"] is True
        assert len(engine.config["sync"]["adapters"]["github"]["repos"]) == 1

    def test_local_settings_override(self, tmp_path):
        """settings.local.yaml overrides settings.yaml."""
        config_dir = tmp_path / ".datacore"
        config_dir.mkdir()

        settings = config_dir / "settings.yaml"
        settings.write_text("""
sync:
  tasks:
    enabled: false
""")

        local_settings = config_dir / "settings.local.yaml"
        local_settings.write_text("""
sync:
  tasks:
    enabled: true
""")

        engine = SyncEngine(data_dir=str(tmp_path))
        engine.config_dir = config_dir
        engine.load_config()

        assert engine.config["sync"]["tasks"]["enabled"] is True


class TestSyncEnginePull:
    """Test pull operations."""

    def test_pull_all_no_adapters(self, tmp_path):
        """Returns empty list when no adapters configured."""
        engine = SyncEngine(data_dir=str(tmp_path))
        changes = engine.pull_all()
        assert changes == []

    def test_pull_all_with_adapter(self, tmp_path):
        """Aggregates changes from all adapters."""
        engine = SyncEngine(data_dir=str(tmp_path))

        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.pull_changes.return_value = [
            TaskChange(
                change_type=ChangeType.CREATED,
                external_task=ExternalTask(
                    id="1",
                    title="Test Issue",
                    state="open",
                    url="https://github.com/test/repo/issues/1",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                ),
                timestamp=datetime.now()
            )
        ]

        engine.adapters["github"] = mock_adapter

        changes = engine.pull_all()
        assert len(changes) == 1
        assert changes[0].external_task.title == "Test Issue"


class TestSyncEnginePush:
    """Test push operations."""

    def test_push_all_routes_by_external_id(self, tmp_path):
        """Routes changes to correct adapter based on external_id prefix."""
        engine = SyncEngine(data_dir=str(tmp_path))

        mock_adapter = MagicMock()
        mock_adapter.push_changes.return_value = SyncResult(
            success=True,
            items_processed=1,
            items_updated=1
        )

        engine.adapters["github"] = mock_adapter

        from sync.adapters import OrgTask, TaskState

        changes = [
            TaskChange(
                change_type=ChangeType.STATE_CHANGED,
                org_task=OrgTask(
                    id="test-1",
                    title="Test Task",
                    state=TaskState.DONE,
                    external_id="github:test/repo#1"
                ),
                timestamp=datetime.now()
            )
        ]

        result = engine.push_all(changes)
        assert result.items_processed == 1
        mock_adapter.push_changes.assert_called_once()


class TestSyncEngineDiagnostic:
    """Test diagnostic functionality."""

    def test_diagnostic_no_adapters(self, tmp_path):
        """Returns basic diagnostic when no adapters."""
        engine = SyncEngine(data_dir=str(tmp_path))

        diag = engine.diagnostic()

        assert "enabled" in diag
        assert "adapters" in diag
        assert diag["adapters"] == {}

    def test_diagnostic_with_adapter(self, tmp_path):
        """Includes adapter status in diagnostic."""
        engine = SyncEngine(data_dir=str(tmp_path))

        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_connection.return_value = (True, "Connected")

        engine.adapters["github"] = mock_adapter

        diag = engine.diagnostic()

        assert "github" in diag["adapters"]
        assert diag["adapters"]["github"]["connected"] is True
        assert diag["adapters"]["github"]["message"] == "Connected"


class TestSyncEngineSync:
    """Test full sync operation."""

    def test_sync_returns_stats(self, tmp_path):
        """Full sync returns statistics."""
        engine = SyncEngine(data_dir=str(tmp_path))

        stats = engine.sync()

        assert "success" in stats
        assert "pull" in stats
        assert "push" in stats
        assert "timestamp" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
