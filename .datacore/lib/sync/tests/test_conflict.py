"""
Tests for the sync conflict queue.

DIP-0010: Task Sync Architecture - Phase 2. The detector and resolver tests
were removed with those classes on 2026-09-23 (owner decision P7); the
specification they must meet when rebuilt is Reconcile.lean `resolve3`.
"""

import pytest
from pathlib import Path
import sys
import tempfile
import os

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sync.conflict import (
    ConflictType,
    ConflictStrategy,
    ConflictField,
    Conflict,
    ConflictQueue,
)


class TestConflictQueue:
    """Tests for ConflictQueue."""

    def setup_method(self):
        """Set up test fixtures with temp database."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_conflicts.db")
        self.queue = ConflictQueue(db_path=self.db_path)

    def teardown_method(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_conflict(self) -> Conflict:
        """Create test conflict."""
        return Conflict(
            external_id="github:owner/repo#1",
            org_task_id="test-task-1",
            fields=[
                ConflictField(
                    field_name="state",
                    conflict_type=ConflictType.STATE,
                    org_value="DONE",
                    external_value="open"
                )
            ]
        )

    def test_add_conflict(self):
        """Test adding conflict to queue."""
        conflict = self.create_conflict()

        conflict_id = self.queue.add(conflict)

        assert conflict_id > 0
        assert conflict.id == conflict_id

    def test_get_unresolved(self):
        """Test getting unresolved conflicts."""
        conflict = self.create_conflict()
        self.queue.add(conflict)

        unresolved = self.queue.get_unresolved()

        assert len(unresolved) == 1
        assert unresolved[0].external_id == "github:owner/repo#1"

    def test_resolve_conflict(self):
        """Test resolving a conflict."""
        conflict = self.create_conflict()
        conflict_id = self.queue.add(conflict)

        success = self.queue.resolve(
            conflict_id,
            ConflictStrategy.ORG_WINS,
            resolved_by="human",
            notes="Manual resolution"
        )

        assert success

        # Should no longer be in unresolved
        unresolved = self.queue.get_unresolved()
        assert len(unresolved) == 0

    def test_get_by_external_id(self):
        """Test getting conflict by external ID."""
        conflict = self.create_conflict()
        self.queue.add(conflict)

        found = self.queue.get_by_external_id("github:owner/repo#1")

        assert found is not None
        assert found.external_id == "github:owner/repo#1"

    def test_get_stats(self):
        """Test getting conflict statistics."""
        conflict = self.create_conflict()
        self.queue.add(conflict)

        stats = self.queue.get_stats()

        assert stats["unresolved"] == 1
        assert "by_type" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
