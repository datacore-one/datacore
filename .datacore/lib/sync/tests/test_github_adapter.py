"""
Tests for GitHub adapter.

DIP-0010: Task Sync Architecture
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lib to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sync.adapters.github import GitHubAdapter
from sync.adapters import OrgTask, TaskState, Priority, ChangeType


class TestGitHubAdapterInit:
    """Test GitHubAdapter initialization."""

    def test_default_config(self):
        """Uses default label mappings."""
        adapter = GitHubAdapter({})

        assert adapter.label_mapping[":AI:"] == "ai-task"
        assert adapter.label_mapping["[#A]"] == "priority-high"

    def test_custom_repos(self):
        """Accepts custom repo configuration."""
        adapter = GitHubAdapter({
            "repos": [
                {"owner": "test", "repo": "test-repo"}
            ]
        })

        assert len(adapter.repos) == 1
        assert adapter.repos[0]["owner"] == "test"


class TestGitHubAdapterConnection:
    """Test connection functionality."""

    def test_is_configured_with_repos(self):
        """Returns True when repos configured."""
        adapter = GitHubAdapter({
            "repos": [{"owner": "test", "repo": "test-repo"}]
        })

        assert adapter.is_configured() is True

    def test_is_configured_without_repos(self):
        """Returns False when no repos."""
        adapter = GitHubAdapter({})

        assert adapter.is_configured() is False

    @patch("subprocess.run")
    def test_test_connection_success(self, mock_run):
        """Returns success when gh CLI authenticated."""
        mock_run.return_value = MagicMock(returncode=0)

        adapter = GitHubAdapter({})
        success, message = adapter.test_connection()

        assert success is True
        assert "authenticated" in message.lower()

    @patch("subprocess.run")
    def test_test_connection_not_authenticated(self, mock_run):
        """Returns failure when not authenticated."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="not authenticated"
        )

        adapter = GitHubAdapter({})
        success, message = adapter.test_connection()

        assert success is False


class TestGitHubAdapterParsing:
    """Test issue parsing."""

    def test_parse_issue(self):
        """Parses gh CLI JSON output correctly."""
        adapter = GitHubAdapter({})

        issue_data = {
            "number": 42,
            "title": "Test Issue",
            "state": "OPEN",
            "url": "https://github.com/test/repo/issues/42",
            "createdAt": "2025-12-09T10:00:00Z",
            "updatedAt": "2025-12-09T11:00:00Z",
            "body": "Test body",
            "labels": [{"name": "ai-task"}, {"name": "priority-high"}],
            "assignees": [{"login": "testuser"}]
        }

        task = adapter._parse_issue(issue_data, "test", "repo")

        assert task.id == "42"
        assert task.title == "Test Issue"
        assert task.state == "open"
        assert "ai-task" in task.labels
        assert task.assignee == "testuser"

    def test_make_external_id(self):
        """Creates correct external ID format."""
        adapter = GitHubAdapter({})

        external_id = adapter._make_external_id("owner", "repo", 42)

        assert external_id == "github:owner/repo#42"

    def test_parse_external_id(self):
        """Parses external ID correctly."""
        adapter = GitHubAdapter({})

        result = adapter._parse_external_id("github:owner/repo#42")

        assert result == ("owner", "repo", 42)

    def test_parse_external_id_invalid(self):
        """Returns None for invalid external ID."""
        adapter = GitHubAdapter({})

        result = adapter._parse_external_id("invalid")

        assert result is None


class TestGitHubAdapterPull:
    """Test pull operations."""

    @patch.object(GitHubAdapter, "_run_gh")
    def test_pull_changes_open_issues(self, mock_run_gh):
        """Pulls open issues correctly."""
        adapter = GitHubAdapter({
            "repos": [{"owner": "test", "repo": "repo"}]
        })

        # Mock open issues response
        open_issues = json.dumps([{
            "number": 1,
            "title": "Test Issue",
            "state": "OPEN",
            "url": "https://github.com/test/repo/issues/1",
            "createdAt": "2025-12-09T10:00:00Z",
            "updatedAt": "2025-12-09T11:00:00Z",
            "body": "Test",
            "labels": [],
            "assignees": []
        }])

        # Mock closed issues response (empty)
        closed_issues = json.dumps([])

        mock_run_gh.side_effect = [
            (True, open_issues, ""),
            (True, closed_issues, "")
        ]

        changes = adapter.pull_changes()

        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.UPDATED
        assert changes[0].external_task.title == "Test Issue"


class TestGitHubAdapterPush:
    """Test push operations."""

    @patch.object(GitHubAdapter, "_run_gh")
    def test_create_task(self, mock_run_gh):
        """Creates GitHub issue from org task."""
        adapter = GitHubAdapter({
            "repos": [{"owner": "test", "repo": "repo"}]
        })

        mock_run_gh.return_value = (
            True,
            "https://github.com/test/repo/issues/42",
            ""
        )

        task = OrgTask(
            id="local-1",
            title="New Task",
            state=TaskState.TODO,
            tags=[":AI:"],
            body="Task description"
        )

        ref = adapter.create_task(task)

        assert ref is not None
        assert ref.external_id == "github:test/repo#42"
        assert "issues/42" in ref.url

    @patch.object(GitHubAdapter, "_run_gh")
    def test_close_task(self, mock_run_gh):
        """Closes GitHub issue."""
        adapter = GitHubAdapter({})

        mock_run_gh.return_value = (True, "", "")

        from sync.adapters import ExternalTaskRef
        ref = ExternalTaskRef(
            adapter="github",
            external_id="github:test/repo#42",
            url="https://github.com/test/repo/issues/42"
        )

        success = adapter.close_task(ref)

        assert success is True
        mock_run_gh.assert_called_once()
        call_args = mock_run_gh.call_args[0][0]
        assert "close" in call_args
        assert "42" in call_args


class TestGitHubAdapterMapping:
    """Test org ↔ GitHub mapping."""

    def test_map_priority_to_external(self):
        """Maps org priority to GitHub label."""
        adapter = GitHubAdapter({})

        assert adapter.map_priority_to_external(Priority.A) == "priority-high"
        assert adapter.map_priority_to_external(Priority.B) == "priority-medium"
        assert adapter.map_priority_to_external(Priority.C) == "priority-low"

    def test_map_state_to_external(self):
        """Maps org state to GitHub state."""
        adapter = GitHubAdapter({})

        assert adapter.map_state_to_external(TaskState.TODO) == "open"
        assert adapter.map_state_to_external(TaskState.DONE) == "closed"
        assert adapter.map_state_to_external(TaskState.CANCELLED) == "closed"


class TestGitHubAdapterSearch:
    """Test duplicate detection."""

    @patch.object(GitHubAdapter, "_run_gh")
    def test_find_matching_task_exact_match(self, mock_run_gh):
        """Finds issue with exact title match."""
        adapter = GitHubAdapter({
            "repos": [{"owner": "test", "repo": "repo"}]
        })

        mock_run_gh.return_value = (
            True,
            json.dumps([{
                "number": 42,
                "title": "Test Task",
                "url": "https://github.com/test/repo/issues/42"
            }]),
            ""
        )

        task = OrgTask(
            id="local-1",
            title="Test Task",
            state=TaskState.TODO
        )

        ref = adapter.find_matching_task(task)

        assert ref is not None
        assert ref.external_id == "github:test/repo#42"

    @patch.object(GitHubAdapter, "_run_gh")
    def test_find_matching_task_no_match(self, mock_run_gh):
        """Returns None when no match found."""
        adapter = GitHubAdapter({
            "repos": [{"owner": "test", "repo": "repo"}]
        })

        mock_run_gh.return_value = (True, json.dumps([]), "")

        task = OrgTask(
            id="local-1",
            title="Nonexistent Task",
            state=TaskState.TODO
        )

        ref = adapter.find_matching_task(task)

        assert ref is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
