"""
Tests for TaskRouter.

DIP-0010: Task Sync Architecture
"""

from datetime import datetime
from pathlib import Path

import pytest

# Add lib to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sync.router import TaskRouter, RoutingRule
from sync.adapters import ExternalTask, TaskState


class TestRoutingRules:
    """Test routing rule parsing."""

    def test_default_rules(self):
        """Creates default rules when none configured."""
        router = TaskRouter({})

        assert len(router.rules) == 3  # ai-task, priority-high, default
        assert router.rules[0].condition == "labels contains 'ai-task'"
        assert router.rules[0].destination == "next_actions.org"

    def test_custom_rules(self):
        """Parses custom rules from config."""
        router = TaskRouter({
            "routing": [
                {
                    "source": "github",
                    "condition": "labels contains 'bug'",
                    "destination": "inbox.org",
                    "tags": [":bug:"]
                }
            ]
        })

        assert len(router.rules) == 1
        assert router.rules[0].source == "github"
        assert router.rules[0].tags == [":bug:"]


class TestConditionEvaluation:
    """Test condition evaluation logic."""

    def test_true_condition(self):
        """'true' always matches."""
        router = TaskRouter({})
        task = ExternalTask(
            id="1",
            title="Test",
            state="open",
            url="https://test.com",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        assert router._evaluate_condition("true", task) is True

    def test_labels_contains(self):
        """Matches task with label."""
        router = TaskRouter({})
        task = ExternalTask(
            id="1",
            title="Test",
            state="open",
            url="https://test.com",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            labels=["ai-task", "priority-high"]
        )

        assert router._evaluate_condition("labels contains 'ai-task'", task) is True
        assert router._evaluate_condition("labels contains 'bug'", task) is False

    def test_assignee_equals(self):
        """Matches task with specific assignee."""
        router = TaskRouter({})
        task = ExternalTask(
            id="1",
            title="Test",
            state="open",
            url="https://test.com",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            assignee="testuser"
        )

        assert router._evaluate_condition("assignee == 'testuser'", task) is True
        assert router._evaluate_condition("assignee == 'other'", task) is False

    def test_assignee_is_null(self):
        """Matches unassigned task."""
        router = TaskRouter({})

        unassigned = ExternalTask(
            id="1",
            title="Test",
            state="open",
            url="https://test.com",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            assignee=None
        )

        assigned = ExternalTask(
            id="2",
            title="Test 2",
            state="open",
            url="https://test.com",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            assignee="user"
        )

        assert router._evaluate_condition("assignee is null", unassigned) is True
        assert router._evaluate_condition("assignee is null", assigned) is False


class TestTaskRouting:
    """Test task routing logic."""

    def test_route_ai_task(self):
        """Routes ai-task labeled issues to next_actions.org."""
        router = TaskRouter({})
        task = ExternalTask(
            id="42",
            title="AI Task",
            state="open",
            url="https://github.com/test/repo/issues/42",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            labels=["ai-task"]
        )

        routing = router.route(task, "github")

        assert routing["destination"] == "next_actions.org"
        assert ":AI:" in routing["tags"]

    def test_route_default_to_inbox(self):
        """Routes unlabeled issues to inbox.org."""
        router = TaskRouter({})
        task = ExternalTask(
            id="42",
            title="Regular Issue",
            state="open",
            url="https://github.com/test/repo/issues/42",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            labels=[]
        )

        routing = router.route(task, "github")

        assert routing["destination"] == "inbox.org"

    def test_source_filtering(self):
        """Respects source filter in rules."""
        router = TaskRouter({
            "routing": [
                {
                    "source": "linear",
                    "condition": "true",
                    "destination": "linear.org"
                },
                {
                    "source": "*",
                    "condition": "true",
                    "destination": "inbox.org"
                }
            ]
        })

        task = ExternalTask(
            id="1",
            title="Test",
            state="open",
            url="https://test.com",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        # From github should match wildcard, not linear
        routing = router.route(task, "github")
        assert routing["destination"] == "inbox.org"


class TestExternalToOrg:
    """Test external task to org task conversion."""

    def test_basic_conversion(self):
        """Converts external task to org task."""
        router = TaskRouter({})
        task = ExternalTask(
            id="42",
            title="Test Issue",
            state="open",
            url="https://github.com/test/repo/issues/42",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            body="Issue description"
        )

        org_task = router.external_to_org(task, "github", "github:test/repo#42")

        assert org_task.title == "Test Issue"
        assert org_task.state == TaskState.TODO
        assert org_task.external_id == "github:test/repo#42"
        assert "EXTERNAL_ID" in org_task.properties

    def test_closed_state_mapping(self):
        """Maps closed external state to DONE."""
        router = TaskRouter({})
        task = ExternalTask(
            id="42",
            title="Closed Issue",
            state="closed",
            url="https://github.com/test/repo/issues/42",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        org_task = router.external_to_org(task, "github", "github:test/repo#42")

        assert org_task.state == TaskState.DONE

    def test_priority_from_labels(self):
        """Extracts priority from labels."""
        router = TaskRouter({})
        task = ExternalTask(
            id="42",
            title="High Priority",
            state="open",
            url="https://github.com/test/repo/issues/42",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            labels=["priority-high"]
        )

        org_task = router.external_to_org(task, "github", "github:test/repo#42")

        from sync.adapters import Priority
        assert org_task.priority == Priority.A

    def test_ai_label_to_tag(self):
        """Converts ai-* labels to :AI:* tags."""
        router = TaskRouter({})
        task = ExternalTask(
            id="42",
            title="AI Research Task",
            state="open",
            url="https://github.com/test/repo/issues/42",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            labels=["ai-research"]
        )

        org_task = router.external_to_org(task, "github", "github:test/repo#42")

        assert ":AI:research:" in org_task.tags


class TestOrgFilePath:
    """Test org file path resolution."""

    def test_default_space(self, tmp_path):
        """Uses 0-personal as default space."""
        router = TaskRouter({}, data_dir=str(tmp_path))

        path = router.get_org_file_path("inbox.org")

        assert path == tmp_path / "0-personal" / "org" / "inbox.org"

    def test_custom_space(self, tmp_path):
        """Accepts custom space."""
        router = TaskRouter({}, data_dir=str(tmp_path))

        path = router.get_org_file_path("next_actions.org", space="1-datafund")

        assert path == tmp_path / "1-datafund" / "org" / "next_actions.org"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
