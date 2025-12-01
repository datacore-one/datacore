"""
Tests for SyncHistory.

DIP-0010: Task Sync Architecture
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add lib to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sync.history import SyncHistory, SyncHistoryEntry


class TestSyncHistoryInit:
    """Test SyncHistory initialization."""

    def test_creates_database(self, tmp_path):
        """Creates database file on init."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        assert db_path.exists()

    def test_creates_tables(self, tmp_path):
        """Creates required tables."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "sync_history" in tables
        assert "sync_state" in tables


class TestSyncHistoryRecord:
    """Test recording sync operations."""

    def test_record_returns_id(self, tmp_path):
        """Returns ID of new record."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        record_id = history.record(
            direction="pull",
            adapter="github",
            items_processed=10,
            items_created=5
        )

        assert record_id is not None
        assert record_id > 0

    def test_record_stores_all_fields(self, tmp_path):
        """Stores all provided fields."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(
            direction="push",
            adapter="github",
            items_processed=10,
            items_created=5,
            items_updated=3,
            items_failed=2,
            errors=["Error 1", "Error 2"],
            duration_ms=1500
        )

        # Verify stored
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM sync_history").fetchone()
        conn.close()

        assert row["direction"] == "push"
        assert row["adapter"] == "github"
        assert row["items_processed"] == 10
        assert row["items_created"] == 5
        assert row["items_updated"] == 3
        assert row["items_failed"] == 2
        assert row["duration_ms"] == 1500
        assert "Error 1" in row["errors"]


class TestSyncHistoryQuery:
    """Test querying sync history."""

    def test_get_last_sync(self, tmp_path):
        """Returns timestamp of last sync."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(direction="pull", adapter="github")
        history.record(direction="push", adapter="github")

        last = history.get_last_sync()

        assert last is not None
        assert isinstance(last, datetime)

    def test_get_last_sync_filtered(self, tmp_path):
        """Filters by adapter and direction."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(direction="pull", adapter="github")
        history.record(direction="push", adapter="calendar")

        last_github_pull = history.get_last_sync(adapter="github", direction="pull")
        last_calendar = history.get_last_sync(adapter="calendar")

        assert last_github_pull is not None
        assert last_calendar is not None

    def test_get_last_sync_empty(self, tmp_path):
        """Returns None when no history."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        last = history.get_last_sync()

        assert last is None

    def test_get_history(self, tmp_path):
        """Returns history entries."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(direction="pull", adapter="github", items_processed=5)
        history.record(direction="pull", adapter="github", items_processed=10)

        entries = history.get_history(days=7)

        assert len(entries) == 2
        assert isinstance(entries[0], SyncHistoryEntry)
        # Most recent first
        assert entries[0].items_processed == 10

    def test_get_history_filtered(self, tmp_path):
        """Filters history by adapter."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(direction="pull", adapter="github")
        history.record(direction="pull", adapter="calendar")

        github_entries = history.get_history(adapter="github")
        calendar_entries = history.get_history(adapter="calendar")

        assert len(github_entries) == 1
        assert len(calendar_entries) == 1


class TestSyncHistoryStats:
    """Test statistics generation."""

    def test_get_stats(self, tmp_path):
        """Returns aggregated statistics."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(direction="pull", adapter="github", items_processed=10)
        history.record(direction="push", adapter="github", items_processed=5)
        history.record(direction="pull", adapter="github", items_processed=15, items_failed=1)

        stats = history.get_stats(days=7)

        assert stats["period_days"] == 7
        assert stats["pull"]["items"] == 25  # 10 + 15
        assert stats["push"]["items"] == 5
        assert "github" in stats["by_adapter"]
        assert stats["last_sync"] is not None

    def test_get_stats_includes_errors(self, tmp_path):
        """Includes recent errors in stats."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.record(
            direction="pull",
            adapter="github",
            errors=["Connection timeout"]
        )

        stats = history.get_stats()

        assert len(stats["errors"]) == 1
        assert "Connection timeout" in stats["errors"]


class TestSyncHistoryState:
    """Test key-value state storage."""

    def test_set_get_state(self, tmp_path):
        """Stores and retrieves state values."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.set_state("last_cursor", "abc123")
        value = history.get_state("last_cursor")

        assert value == "abc123"

    def test_get_state_missing(self, tmp_path):
        """Returns None for missing key."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        value = history.get_state("nonexistent")

        assert value is None

    def test_set_state_overwrites(self, tmp_path):
        """Overwrites existing value."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        history.set_state("key", "value1")
        history.set_state("key", "value2")

        assert history.get_state("key") == "value2"


class TestSyncHistoryCleanup:
    """Test history cleanup."""

    def test_cleanup_removes_old_entries(self, tmp_path):
        """Removes entries older than specified days."""
        db_path = tmp_path / "sync_history.db"
        history = SyncHistory(db_path=str(db_path))

        # Insert old entry directly
        conn = sqlite3.connect(str(db_path))
        old_timestamp = (datetime.now() - timedelta(days=45)).isoformat()
        conn.execute("""
            INSERT INTO sync_history (timestamp, direction, adapter, items_processed)
            VALUES (?, 'pull', 'github', 1)
        """, (old_timestamp,))
        conn.commit()
        conn.close()

        # Insert recent entry
        history.record(direction="pull", adapter="github")

        # Cleanup entries older than 30 days
        history.cleanup(days=30)

        entries = history.get_history(days=60)
        assert len(entries) == 1  # Only recent entry remains


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
