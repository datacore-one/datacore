"""Tests for org_workspace_adapter.py enhancements — move, show, update, enhanced add."""

import json
import subprocess
import shutil
from pathlib import Path

import pytest

ADAPTER = Path(__file__).parent.parent / "org_workspace_adapter.py"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def work_dir(tmp_path):
    """Copy fixtures to a temp dir for mutation tests."""
    shutil.copy(FIXTURES / "inbox.org", tmp_path / "inbox.org")
    shutil.copy(FIXTURES / "next_actions.org", tmp_path / "next_actions.org")
    return tmp_path


def run_adapter(*args):
    result = subprocess.run(
        ["python3", str(ADAPTER)] + list(args),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Adapter failed: {result.stderr}"
    return json.loads(result.stdout)


class TestAddEnhanced:
    def test_add_with_body(self, work_dir):
        result = run_adapter(
            "add", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Test task with body",
            "--body", "This is the body text.\nSecond line.",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "This is the body text." in content
        assert "Second line." in content

    def test_add_with_properties(self, work_dir):
        result = run_adapter(
            "add", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Task with context",
            "--property", "CONTEXT=Demo recorded",
            "--property", "EFFORT=0:30",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert ":CONTEXT: Demo recorded" in content
        assert ":EFFORT: 0:30" in content

    def test_add_with_parent(self, work_dir):
        result = run_adapter(
            "add", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Child of AI Queue",
            "--parent", "AI Queue",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "** TODO Child of AI Queue" in content

    def test_add_with_parent_by_id(self, work_dir):
        result = run_adapter(
            "add", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Child by ID",
            "--parent-id", "fds-001",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "**** TODO Child by ID" in content


class TestMove:
    def test_move_by_id(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "inbox-001",
        )
        assert result["moved"] is True
        assert result["id"] == "inbox-001"
        inbox = (work_dir / "inbox.org").read_text()
        assert "inbox-001" not in inbox
        na = (work_dir / "next_actions.org").read_text()
        assert "inbox-001" in na
        assert "Simple inbox item" in na

    def test_move_by_title(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--title", "Rich inbox",
        )
        assert result["moved"] is True
        na = (work_dir / "next_actions.org").read_text()
        assert ":CONTEXT: Demo is recorded and working." in na
        assert "review for secrets" in na

    def test_move_with_parent(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "inbox-001",
            "--parent", "AI Queue",
        )
        assert result["moved"] is True
        na = (work_dir / "next_actions.org").read_text()
        assert "** TODO Simple inbox item" in na

    def test_move_not_found(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "nonexistent-id",
        )
        assert "error" in result


class TestShow:
    def test_show_by_id(self, work_dir):
        result = run_adapter(
            "show", "--file", str(work_dir / "inbox.org"),
            "--id", "inbox-002",
        )
        assert result["heading"] == "Rich inbox item"
        assert result["state"] == "TODO"
        assert result["properties"]["CONTEXT"] == "Demo is recorded and working."
        assert "review for secrets" in result["body"]

    def test_show_by_title(self, work_dir):
        result = run_adapter(
            "show", "--file", str(work_dir / "inbox.org"),
            "--title", "Simple inbox",
        )
        assert result["heading"] == "Simple inbox item"
        assert "Body of simple item" in result["body"]

    def test_show_not_found(self, work_dir):
        result = run_adapter(
            "show", "--file", str(work_dir / "inbox.org"),
            "--id", "nonexistent",
        )
        assert "error" in result


class TestUpdate:
    def test_update_scheduled(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--scheduled", "2026-05-01",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "SCHEDULED: <2026-05-01 Fri>" in content

    def test_update_property(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--property", "EFFORT=2:00",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert ":EFFORT: 2:00" in content

    def test_update_tags(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--tags", ":AI:research:",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert ":AI:research:" in content

    def test_update_state(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--state", "NEXT",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "NEXT" in content

    def test_update_not_found(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "nonexistent",
            "--state", "DONE",
        )
        assert "error" in result


class TestInboxProcessing:
    """End-to-end: process inbox items like GTD triage."""

    def test_full_inbox_to_next_actions_flow(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "inbox-002",
            "--parent", "AI Queue",
        )
        assert result["moved"] is True

        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "inbox-002",
            "--scheduled", "2026-04-23",
        )
        assert result["updated"] is True

        result = run_adapter(
            "show", "--file", str(work_dir / "next_actions.org"),
            "--id", "inbox-002",
        )
        assert result["heading"] == "Rich inbox item"
        assert result["properties"]["CONTEXT"] == "Demo is recorded and working."
        assert result["scheduled"] == "2026-04-23"
        assert "review for secrets" in result["body"]

        inbox = (work_dir / "inbox.org").read_text()
        assert "inbox-002" not in inbox

    def test_add_new_task_with_full_context(self, work_dir):
        result = run_adapter(
            "add", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Post video on X",
            "--priority", "C",
            "--tags", ":fds:comms:",
            "--scheduled", "2026-04-20",
            "--parent", "AI Queue",
            "--body", "Tag @jssr. Share with Tether contact via DM.",
            "--property", "CONTEXT=Strategy from engram",
        )
        assert result["added"] is True

        result = run_adapter(
            "show", "--file", str(work_dir / "next_actions.org"),
            "--id", result["id"],
        )
        assert result["properties"]["CONTEXT"] == "Strategy from engram"
        assert "Tag @jssr" in result["body"]
