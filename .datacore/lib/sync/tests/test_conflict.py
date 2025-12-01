"""
Tests for conflict detection and resolution.

DIP-0010: Task Sync Architecture - Phase 2
"""

import pytest
from datetime import datetime, timedelta
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
    ConflictDetector,
    ConflictResolver,
    ConflictQueue,
)
from sync.adapters.base import OrgTask, ExternalTask, TaskState, Priority


class TestConflictDetector:
    """Tests for ConflictDetector."""

    def setup_method(self):
        """Set up test fixtures."""
        self.detector = ConflictDetector()

    def create_org_task(self, **overrides) -> OrgTask:
        """Create test org task."""
        defaults = {
            "id": "test-task-1",
            "title": "Test Task",
            "state": TaskState.TODO,
            "priority": Priority.B,
            "body": "Test description",
            "tags": [":AI:"],
            "external_id": "github:owner/repo#1",
        }
        defaults.update(overrides)
        return OrgTask(**defaults)

    def create_external_task(self, **overrides) -> ExternalTask:
        """Create test external task."""
        defaults = {
            "id": "1",
            "title": "Test Task",
            "state": "open",
            "url": "https://github.com/owner/repo/issues/1",
            "created_at": datetime.now() - timedelta(days=1),
            "updated_at": datetime.now(),
            "body": "Test description",
            "labels": ["priority-medium"],
        }
        defaults.update(overrides)
        return ExternalTask(**defaults)

    def test_no_conflict_when_same(self):
        """No conflict when tasks are identical."""
        org_task = self.create_org_task()
        external_task = self.create_external_task()

        conflict = self.detector.detect(org_task, external_task)

        assert conflict is None

    def test_state_conflict_detected(self):
        """Detect state conflict when one is closed, other is open."""
        org_task = self.create_org_task(state=TaskState.DONE)
        external_task = self.create_external_task(state="open")

        conflict = self.detector.detect(org_task, external_task)

        assert conflict is not None
        assert ConflictType.STATE in conflict.conflict_types

    def test_title_conflict_detected(self):
        """Detect title conflict."""
        org_task = self.create_org_task(title="Updated Title")
        external_task = self.create_external_task(title="Different Title")

        conflict = self.detector.detect(org_task, external_task)

        assert conflict is not None
        assert ConflictType.TITLE in conflict.conflict_types

    def test_priority_conflict_detected(self):
        """Detect priority conflict."""
        org_task = self.create_org_task(priority=Priority.A)
        external_task = self.create_external_task(labels=["priority-low"])

        conflict = self.detector.detect(org_task, external_task)

        assert conflict is not None
        assert ConflictType.PRIORITY in conflict.conflict_types

    def test_description_conflict_detected(self):
        """Detect description conflict when both have different content."""
        org_task = self.create_org_task(body="Org description content")
        external_task = self.create_external_task(body="External description content")

        conflict = self.detector.detect(org_task, external_task)

        assert conflict is not None
        assert ConflictType.DESCRIPTION in conflict.conflict_types

    def test_no_description_conflict_when_one_empty(self):
        """No conflict when only one has description (one-way update)."""
        org_task = self.create_org_task(body="Org description")
        external_task = self.create_external_task(body="")

        conflict = self.detector.detect(org_task, external_task)

        # Should not have description conflict (one-way update)
        if conflict:
            assert ConflictType.DESCRIPTION not in conflict.conflict_types

    def test_multiple_conflicts_detected(self):
        """Detect multiple conflicts at once."""
        org_task = self.create_org_task(
            state=TaskState.DONE,
            title="Org Title",
            priority=Priority.A
        )
        external_task = self.create_external_task(
            state="open",
            title="External Title",
            labels=["priority-low"]
        )

        conflict = self.detector.detect(org_task, external_task)

        assert conflict is not None
        assert len(conflict.fields) >= 2


class TestConflictResolver:
    """Tests for ConflictResolver."""

    def setup_method(self):
        """Set up test fixtures."""
        self.resolver = ConflictResolver()

    def create_conflict(self, fields: list) -> Conflict:
        """Create test conflict."""
        return Conflict(
            external_id="github:owner/repo#1",
            org_task_id="test-task-1",
            fields=fields
        )

    def test_org_wins_strategy(self):
        """Test ORG_WINS strategy."""
        conflict = self.create_conflict([
            ConflictField(
                field_name="state",
                conflict_type=ConflictType.STATE,
                org_value="DONE",
                external_value="open"
            )
        ])

        resolution = self.resolver.resolve(conflict)

        assert resolution.external_changes.get("state") == "DONE"
        assert not resolution.needs_human_review

    def test_external_wins_strategy(self):
        """Test EXTERNAL_WINS strategy for comments."""
        resolver = ConflictResolver({ConflictType.COMMENTS: ConflictStrategy.EXTERNAL_WINS})
        conflict = self.create_conflict([
            ConflictField(
                field_name="comments",
                conflict_type=ConflictType.COMMENTS,
                org_value="org comment",
                external_value="external comment"
            )
        ])

        resolution = resolver.resolve(conflict)

        assert resolution.org_changes.get("comments") == "external comment"

    def test_merge_strategy_description(self):
        """Test MERGE strategy for descriptions."""
        conflict = self.create_conflict([
            ConflictField(
                field_name="description",
                conflict_type=ConflictType.DESCRIPTION,
                org_value="Org content",
                external_value="External content"
            )
        ])

        resolution = self.resolver.resolve(conflict)

        # Should have merged description in both
        assert "description" in resolution.org_changes or "description" in resolution.external_changes

    def test_ask_strategy_needs_human_review(self):
        """Test ASK strategy flags for human review."""
        resolver = ConflictResolver({ConflictType.STATE: ConflictStrategy.ASK})
        conflict = self.create_conflict([
            ConflictField(
                field_name="state",
                conflict_type=ConflictType.STATE,
                org_value="DONE",
                external_value="open"
            )
        ])

        resolution = resolver.resolve(conflict)

        assert resolution.needs_human_review

    def test_conflict_marked_resolved(self):
        """Test conflict is marked resolved when auto-resolved."""
        conflict = self.create_conflict([
            ConflictField(
                field_name="priority",
                conflict_type=ConflictType.PRIORITY,
                org_value="A",
                external_value="C"
            )
        ])

        resolution = self.resolver.resolve(conflict)

        assert conflict.resolved
        assert conflict.resolved_by == "auto"


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


class TestConflictIntegration:
    """Integration tests for conflict resolution flow."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_conflicts.db")
        self.detector = ConflictDetector()
        self.resolver = ConflictResolver()
        self.queue = ConflictQueue(db_path=self.db_path)

    def teardown_method(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_conflict_flow(self):
        """Test complete flow: detect -> resolve -> queue if needed."""
        # Create conflicting tasks
        org_task = OrgTask(
            id="test-1",
            title="Updated Task",
            state=TaskState.DONE,
            priority=Priority.A,
            body="Org description",
            external_id="github:owner/repo#1"
        )

        external_task = ExternalTask(
            id="1",
            title="Updated Task",
            state="open",
            url="https://github.com/owner/repo/issues/1",
            created_at=datetime.now() - timedelta(days=1),
            updated_at=datetime.now(),
            body="External description",
            labels=["priority-low"]
        )

        # Detect conflicts
        conflict = self.detector.detect(org_task, external_task)
        assert conflict is not None

        # Resolve
        resolution = self.resolver.resolve(conflict)

        # Should have resolution
        assert resolution is not None
        assert len(resolution.external_changes) > 0 or len(resolution.org_changes) > 0

        # If needs review, add to queue
        if resolution.needs_human_review:
            self.queue.add(conflict)
            unresolved = self.queue.get_unresolved()
            assert len(unresolved) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
