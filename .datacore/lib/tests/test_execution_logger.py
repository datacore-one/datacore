"""Tests for execution_logger.py - DIP-0016 Agent Performance Tracking."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from execution_logger import (
    _load_log,
    _save_log,
    _generate_id,
    log_execution,
    log_evaluation,
    log_session_memory,
    get_agent_performance,
)


@pytest.fixture
def log_env(tmp_path, monkeypatch):
    """Set up execution logger with temp paths."""
    log_path = tmp_path / ".datacore" / "state" / "execution_log.yaml"
    monkeypatch.setattr("execution_logger.EXECUTION_LOG_PATH", log_path)
    return log_path


class TestLoadLog:
    """Test log loading."""

    def test_missing_log_returns_default(self, log_env):
        """Missing log file returns default structure."""
        log = _load_log()
        assert log["version"] == "1.0.0"
        assert log["executions"] == []
        assert log["evaluations"] == []

    def test_loads_existing_log(self, log_env):
        """Existing log file is loaded correctly."""
        log_env.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": "1.0.0", "executions": [{"id": "test"}], "evaluations": []}
        with open(log_env, "w") as f:
            yaml.dump(data, f)

        log = _load_log()
        assert len(log["executions"]) == 1


class TestGenerateId:
    """Test ID generation."""

    def test_has_prefix(self):
        """IDs have the specified prefix."""
        id_ = _generate_id("exec")
        assert id_.startswith("exec-")

    def test_has_date(self):
        """IDs contain a date component."""
        id_ = _generate_id("eval")
        # Format: prefix-YYYY-MM-DD-uuid
        parts = id_.split("-")
        assert len(parts) >= 4
        assert len(parts[1]) == 4  # Year

    def test_unique(self):
        """IDs are unique across calls."""
        ids = {_generate_id("test") for _ in range(100)}
        assert len(ids) == 100


class TestLogExecution:
    """Test execution logging."""

    def test_logs_basic_execution(self, log_env):
        """Basic execution is logged with required fields."""
        exec_id = log_execution(
            agent_id="test-agent",
            status="success",
        )
        assert exec_id.startswith("exec-")

        log = _load_log()
        assert len(log["executions"]) == 1
        entry = log["executions"][0]
        assert entry["agent_id"] == "test-agent"
        assert entry["status"] == "success"

    def test_logs_with_all_fields(self, log_env):
        """Execution with all optional fields."""
        exec_id = log_execution(
            agent_id="knowledge-extractor",
            task_id="task-123",
            status="success",
            outputs={"files_created": 3},
            duration_ms=5000,
            triggered_by="ai-task-executor",
            trigger_type="tag",
            trigger_value=":AI:research:",
        )
        log = _load_log()
        entry = log["executions"][0]
        assert entry["task_id"] == "task-123"
        assert entry["duration_ms"] == 5000
        assert entry["outputs"]["files_created"] == 3
        assert entry["context"]["triggered_by"] == "ai-task-executor"

    def test_logs_failure_with_error(self, log_env):
        """Failed execution includes error message."""
        log_execution(
            agent_id="test-agent",
            status="failure",
            error="API rate limit exceeded",
        )
        log = _load_log()
        assert log["executions"][0]["error"] == "API rate limit exceeded"

    def test_multiple_executions_accumulate(self, log_env):
        """Multiple executions are appended."""
        log_execution(agent_id="agent-1", status="success")
        log_execution(agent_id="agent-2", status="success")
        log_execution(agent_id="agent-3", status="failure")

        log = _load_log()
        assert len(log["executions"]) == 3


class TestLogEvaluation:
    """Test evaluation logging."""

    def test_logs_evaluation(self, log_env):
        """Evaluation is logged with scores."""
        eval_id = log_evaluation(
            evaluator="evaluator-cto",
            evaluated="knowledge-extractor",
            execution_id="exec-2026-01-01-abc",
            scores={"completeness": 0.95, "accuracy": 0.90},
            notes="Good extraction quality",
        )
        assert eval_id.startswith("eval-")

        log = _load_log()
        assert len(log["evaluations"]) == 1
        entry = log["evaluations"][0]
        assert entry["scores"]["completeness"] == 0.95
        assert entry["notes"] == "Good extraction quality"


class TestLogSessionMemory:
    """Test session memory logging."""

    def test_logs_memory(self, log_env):
        """Session memory is logged."""
        mem_id = log_session_memory(
            agent_id="session-learning",
            execution_id="exec-123",
            summary="Learned that X causes Y",
            tags=["debugging", "pattern"],
        )
        assert mem_id.startswith("mem-")

        log = _load_log()
        assert len(log["session_memories"]) == 1
        assert log["session_memories"][0]["tags"] == ["debugging", "pattern"]


class TestGetAgentPerformance:
    """Test performance metric computation."""

    def test_empty_log_returns_zeros(self, log_env):
        """Empty log returns zero metrics."""
        metrics = get_agent_performance("test-agent")
        assert metrics["executions"] == 0
        assert metrics["success_rate"] == 0.0

    def test_computes_success_rate(self, log_env):
        """Success rate is computed correctly."""
        log_execution(agent_id="test-agent", status="success")
        log_execution(agent_id="test-agent", status="success")
        log_execution(agent_id="test-agent", status="failure")

        metrics = get_agent_performance("test-agent")
        assert metrics["executions"] == 3
        assert abs(metrics["success_rate"] - 0.667) < 0.01

    def test_filters_by_agent(self, log_env):
        """Metrics are filtered to the requested agent."""
        log_execution(agent_id="agent-a", status="success")
        log_execution(agent_id="agent-b", status="failure")

        metrics = get_agent_performance("agent-a")
        assert metrics["executions"] == 1
        assert metrics["success_rate"] == 1.0
