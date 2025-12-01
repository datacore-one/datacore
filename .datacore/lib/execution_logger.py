#!/usr/bin/env python3
"""
Execution Logger for DIP-0016 Agent Performance Tracking

This module provides functions to log agent executions, evaluations,
session memories, and reasoning paths to the execution_log.yaml file.

Usage:
    from execution_logger import log_execution, log_evaluation, get_agent_performance

    # Log an execution
    log_execution(
        agent_id="knowledge-extractor",
        task_id="task-123",
        status="success",
        outputs={"files_created": 2},
        duration_ms=135000
    )

    # Log an evaluation
    log_evaluation(
        evaluator="ai-task-executor",
        evaluated="knowledge-extractor",
        execution_id="exec-2025-12-22-001",
        scores={"completeness": 0.95, "accuracy": 0.90}
    )

    # Get performance metrics
    metrics = get_agent_performance("knowledge-extractor")
"""

import os
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
import uuid

# Default paths
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
EXECUTION_LOG_PATH = DATACORE_ROOT / ".datacore" / "state" / "execution_log.yaml"


def _load_log() -> Dict[str, Any]:
    """Load the execution log file."""
    if not EXECUTION_LOG_PATH.exists():
        return {
            "version": "1.0.0",
            "protocol": "DIP-0016",
            "last_sync": None,
            "executions": [],
            "evaluations": [],
            "session_memories": [],
            "reasoning_paths": [],
            "metrics": {"last_computed": None, "period": "weekly", "by_agent": {}}
        }

    with open(EXECUTION_LOG_PATH, 'r') as f:
        return yaml.safe_load(f) or {}


def _save_log(log: Dict[str, Any]) -> None:
    """Save the execution log file."""
    EXECUTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EXECUTION_LOG_PATH, 'w') as f:
        yaml.dump(log, f, default_flow_style=False, sort_keys=False)


def _generate_id(prefix: str) -> str:
    """Generate a unique ID with date prefix."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_uuid = str(uuid.uuid4())[:8]
    return f"{prefix}-{date_str}-{short_uuid}"


def _now_iso() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def log_execution(
    agent_id: str,
    task_id: Optional[str] = None,
    status: str = "success",
    outputs: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
    triggered_by: Optional[str] = None,
    trigger_type: Optional[str] = None,
    trigger_value: Optional[str] = None,
    error: Optional[str] = None,
    started_at: Optional[str] = None
) -> str:
    """
    Log an agent execution.

    Args:
        agent_id: The agent that executed (e.g., "knowledge-extractor")
        task_id: Optional task identifier
        status: "success", "failure", or "partial"
        outputs: Dict of output metrics (files_created, tokens_used, etc.)
        duration_ms: Execution duration in milliseconds
        triggered_by: Agent that triggered this execution
        trigger_type: "tag", "command", or "spawn"
        trigger_value: The tag/command/agent that triggered
        error: Error message if status != success
        started_at: ISO timestamp when execution started

    Returns:
        The execution_id of the logged execution
    """
    log = _load_log()

    execution_id = _generate_id("exec")
    now = _now_iso()

    entry = {
        "execution_id": execution_id,
        "agent_id": agent_id,
        "task_id": task_id,
        "started_at": started_at or now,
        "completed_at": now,
        "duration_ms": duration_ms,
        "status": status,
        "outputs": outputs or {},
        "context": {
            "triggered_by": triggered_by,
            "trigger_type": trigger_type,
            "trigger_value": trigger_value
        },
        "error": error
    }

    log["executions"].append(entry)
    _save_log(log)

    return execution_id


def log_evaluation(
    evaluator: str,
    evaluated: str,
    execution_id: str,
    scores: Dict[str, float],
    notes: Optional[str] = None
) -> str:
    """
    Log an inter-agent evaluation.

    Args:
        evaluator: Agent doing the evaluation
        evaluated: Agent being evaluated
        execution_id: The execution being evaluated
        scores: Dict of score metrics (completeness, accuracy, etc.) - values 0.0-1.0
        notes: Optional notes about the evaluation

    Returns:
        The evaluation_id
    """
    log = _load_log()

    evaluation_id = _generate_id("eval")

    entry = {
        "evaluation_id": evaluation_id,
        "evaluator_agent": evaluator,
        "evaluated_agent": evaluated,
        "execution_id": execution_id,
        "timestamp": _now_iso(),
        "scores": scores,
        "notes": notes
    }

    log["evaluations"].append(entry)
    _save_log(log)

    return evaluation_id


def log_session_memory(
    agent_id: str,
    execution_id: str,
    summary: str,
    tags: Optional[List[str]] = None
) -> str:
    """
    Log a session memory for future retrieval.

    Args:
        agent_id: Agent that generated this memory
        execution_id: Related execution
        summary: Summary of what was accomplished
        tags: Tags for categorization

    Returns:
        The memory_id
    """
    log = _load_log()

    memory_id = _generate_id("mem")

    entry = {
        "memory_id": memory_id,
        "execution_id": execution_id,
        "agent_id": agent_id,
        "timestamp": _now_iso(),
        "summary": summary,
        "tags": tags or [],
        "embedding_id": None,
        "retrievable": False
    }

    log["session_memories"].append(entry)
    _save_log(log)

    return memory_id


def log_reasoning_path(
    agent_id: str,
    execution_id: str,
    hops: List[Dict[str, Any]]
) -> str:
    """
    Log a multi-hop reasoning path.

    Args:
        agent_id: Agent that performed the reasoning
        execution_id: Related execution
        hops: List of hop records, each with:
            - hop: int (1-indexed)
            - query: str
            - source: str (e.g., "datacortex")
            - results: int (number of results)
            - selected: List[str] (IDs of selected documents)

    Returns:
        The path_id
    """
    log = _load_log()

    path_id = _generate_id("path")

    # Calculate total context size (estimate)
    total_context = sum(len(h.get("selected", [])) * 500 for h in hops)  # ~500 tokens per doc

    entry = {
        "path_id": path_id,
        "execution_id": execution_id,
        "agent_id": agent_id,
        "timestamp": _now_iso(),
        "hops": hops,
        "total_hops": len(hops),
        "final_context_size": total_context
    }

    log["reasoning_paths"].append(entry)
    _save_log(log)

    return path_id


def get_agent_performance(
    agent_id: str,
    period_days: int = 7
) -> Dict[str, Any]:
    """
    Get performance metrics for an agent.

    Args:
        agent_id: Agent to get metrics for
        period_days: Number of days to look back

    Returns:
        Dict with metrics:
            - executions: int
            - success_rate: float
            - avg_duration_ms: float
            - avg_completeness: float (if evaluations exist)
    """
    log = _load_log()

    # Filter executions for this agent within period
    cutoff = datetime.now(timezone.utc).timestamp() - (period_days * 86400)

    agent_executions = [
        e for e in log.get("executions", [])
        if e.get("agent_id") == agent_id
        and _parse_timestamp(e.get("completed_at", "")) > cutoff
    ]

    if not agent_executions:
        return {
            "executions": 0,
            "success_rate": 0.0,
            "avg_duration_ms": 0,
            "avg_completeness": None
        }

    # Calculate metrics
    total = len(agent_executions)
    successes = sum(1 for e in agent_executions if e.get("status") == "success")
    durations = [e.get("duration_ms", 0) for e in agent_executions if e.get("duration_ms")]

    # Get evaluations for completeness
    execution_ids = {e.get("execution_id") for e in agent_executions}
    agent_evals = [
        ev for ev in log.get("evaluations", [])
        if ev.get("execution_id") in execution_ids
    ]
    completeness_scores = [
        ev.get("scores", {}).get("completeness")
        for ev in agent_evals
        if ev.get("scores", {}).get("completeness") is not None
    ]

    return {
        "executions": total,
        "success_rate": successes / total if total > 0 else 0.0,
        "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
        "avg_completeness": sum(completeness_scores) / len(completeness_scores) if completeness_scores else None
    }


def _parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp to Unix timestamp."""
    if not ts:
        return 0
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return 0


def get_unembedded_memories() -> List[Dict[str, Any]]:
    """Get session memories that haven't been embedded yet."""
    log = _load_log()
    return [
        m for m in log.get("session_memories", [])
        if not m.get("retrievable", False)
    ]


def mark_memory_embedded(memory_id: str, embedding_id: str) -> None:
    """Mark a session memory as embedded and retrievable."""
    log = _load_log()

    for memory in log.get("session_memories", []):
        if memory.get("memory_id") == memory_id:
            memory["embedding_id"] = embedding_id
            memory["retrievable"] = True
            break

    _save_log(log)


def compute_metrics() -> Dict[str, Any]:
    """Compute and store aggregated metrics for all agents."""
    log = _load_log()

    # Get unique agents
    agents = set(e.get("agent_id") for e in log.get("executions", []))

    by_agent = {}
    for agent_id in agents:
        if agent_id:
            by_agent[agent_id] = get_agent_performance(agent_id)

    log["metrics"] = {
        "last_computed": _now_iso(),
        "period": "weekly",
        "by_agent": by_agent
    }

    _save_log(log)
    return by_agent


if __name__ == "__main__":
    # CLI interface for testing
    import sys

    if len(sys.argv) < 2:
        print("Usage: execution_logger.py <command> [args]")
        print("Commands:")
        print("  log-exec <agent_id> <status>  - Log an execution")
        print("  metrics <agent_id>            - Show agent metrics")
        print("  compute-all                   - Compute all metrics")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "log-exec" and len(sys.argv) >= 4:
        exec_id = log_execution(sys.argv[2], status=sys.argv[3])
        print(f"Logged execution: {exec_id}")

    elif cmd == "metrics" and len(sys.argv) >= 3:
        metrics = get_agent_performance(sys.argv[2])
        print(yaml.dump(metrics, default_flow_style=False))

    elif cmd == "compute-all":
        metrics = compute_metrics()
        print(yaml.dump(metrics, default_flow_style=False))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
