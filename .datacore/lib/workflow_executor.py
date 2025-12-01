#!/usr/bin/env python3
"""
Workflow YAML Executor for Datacore.

Parses and executes workflow YAML files, tracking phase completion state.
Supports tool, agent, interactive, and output phase types with conditions,
skip_if, stop_if, and dry-run mode.

Usage:
    python workflow_executor.py <workflow.yaml> [--dry-run] [--context key=value ...]
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("workflow_executor")

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_FILE = STATE_DIR / "workflow_state.yaml"


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

class WorkflowError(Exception):
    """Raised when a workflow cannot be loaded or executed."""


class Phase:
    """Represents a single phase within a workflow."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.name: str = data["name"]
        self.type: str = data.get("type", "tool")
        self.invoke: str = data.get("invoke", "")
        self.action: str = data.get("action", "")
        self.params: dict[str, Any] = data.get("params", {})
        self.description: str = data.get("description", "")
        self.condition: str | None = data.get("condition")
        self.skip_if: str | None = data.get("skip_if")
        self.stop_if: str | None = data.get("stop_if")
        self.optional: bool = data.get("optional", False)
        self.format: str = data.get("format", "")

    def __repr__(self) -> str:
        return f"Phase({self.name!r}, type={self.type!r})"


class Workflow:
    """Represents a parsed workflow YAML."""

    def __init__(self, data: dict[str, Any], source_path: str = "") -> None:
        self.name: str = data.get("name", "unnamed")
        self.description: str = data.get("description", "")
        self.command: str = data.get("command", "")
        self.version: str = data.get("version", "0.0.0")
        self.trigger: str = data.get("trigger", "manual")
        self.hooks: list[str] = data.get("hooks", [])
        self.source_path: str = source_path

        raw_phases = data.get("phases", [])
        if not isinstance(raw_phases, list):
            raise WorkflowError(f"'phases' must be a list in workflow {self.name}")
        self.phases: list[Phase] = [Phase(p) for p in raw_phases]

    def __repr__(self) -> str:
        return f"Workflow({self.name!r}, phases={len(self.phases)})"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_workflow(yaml_path: str) -> Workflow:
    """Parse a workflow YAML file and return a Workflow object."""
    path = Path(yaml_path)
    if not path.exists():
        raise WorkflowError(f"Workflow file not found: {yaml_path}")

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"Invalid YAML in {yaml_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise WorkflowError(f"Workflow file must contain a YAML mapping: {yaml_path}")

    if "phases" not in data:
        raise WorkflowError(f"Workflow missing 'phases' key: {yaml_path}")

    return Workflow(data, source_path=str(path.resolve()))


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict[str, Any]:
    """Load the workflow state file, creating it if absent."""
    if not STATE_FILE.exists():
        return {"workflows": {}}
    try:
        with open(STATE_FILE) as f:
            state = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        logger.warning("Corrupt workflow state file — resetting")
        state = {}
    if "workflows" not in state:
        state["workflows"] = {}
    return state


def _save_state(state: dict[str, Any]) -> None:
    """Persist the workflow state to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        yaml.safe_dump(state, f, default_flow_style=False, sort_keys=False)


def _update_phase_state(
    workflow_name: str,
    phase_name: str,
    status: str,
    detail: str = "",
) -> None:
    """Record completion state for a single phase."""
    state = _load_state()
    wf_state = state["workflows"].setdefault(workflow_name, {
        "last_run": None,
        "phases": {},
    })
    wf_state["last_run"] = datetime.now(timezone.utc).isoformat()
    wf_state["phases"][phase_name] = {
        "status": status,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(state)


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _evaluate_condition(condition: str | None, context: dict[str, Any]) -> bool:
    """
    Evaluate a simple condition against the execution context.

    Supports:
      - Key presence: "some-key" => True if key is in context and truthy
      - Negation: "no-continuations" => True if "continuations" is falsy or absent
      - Key-value: "status=done" => True if context["status"] == "done"
    """
    if condition is None:
        return True

    condition = condition.strip()
    if not condition:
        return True

    # Negation: "no-<key>" means key must be absent or falsy
    if condition.startswith("no-"):
        negated_key = condition[3:]
        return not context.get(negated_key)

    # Key-value: "key=value"
    if "=" in condition:
        key, value = condition.split("=", 1)
        return str(context.get(key.strip(), "")) == value.strip()

    # Simple presence: key must be truthy
    return bool(context.get(condition))


# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------

def _execute_phase(
    phase: Phase,
    context: dict[str, Any],
    workflow_name: str,
    dry_run: bool = False,
) -> str:
    """
    Execute (or simulate) a single phase.

    Returns a status string: "completed", "skipped", "paused", "stopped", "error".
    """
    # --- stop_if check ---
    if phase.stop_if and _evaluate_condition(phase.stop_if, context):
        msg = f"STOP  — stop_if condition met: {phase.stop_if}"
        logger.info("  [%s] %s", phase.name, msg)
        _update_phase_state(workflow_name, phase.name, "stopped", msg)
        return "stopped"

    # --- skip_if check ---
    if phase.skip_if and _evaluate_condition(phase.skip_if, context):
        msg = f"SKIP  — skip_if condition met: {phase.skip_if}"
        logger.info("  [%s] %s", phase.name, msg)
        _update_phase_state(workflow_name, phase.name, "skipped", msg)
        return "skipped"

    # --- condition check ---
    if phase.condition and not _evaluate_condition(phase.condition, context):
        msg = f"SKIP  — condition not met: {phase.condition}"
        logger.info("  [%s] %s", phase.name, msg)
        _update_phase_state(workflow_name, phase.name, "skipped", msg)
        return "skipped"

    # --- Dispatch by type ---
    if phase.type == "tool":
        return _handle_tool(phase, context, workflow_name, dry_run)
    elif phase.type == "agent":
        return _handle_agent(phase, context, workflow_name, dry_run)
    elif phase.type == "interactive":
        return _handle_interactive(phase, context, workflow_name, dry_run)
    elif phase.type == "output":
        return _handle_output(phase, context, workflow_name, dry_run)
    else:
        msg = f"Unknown phase type: {phase.type}"
        logger.warning("  [%s] %s", phase.name, msg)
        _update_phase_state(workflow_name, phase.name, "error", msg)
        return "error"


def _handle_tool(phase: Phase, context: dict[str, Any], wf: str, dry_run: bool) -> str:
    detail = f"TOOL  invoke={phase.invoke}"
    if phase.params:
        detail += f"  params={phase.params}"
    if dry_run:
        logger.info("  [%s] (dry-run) %s", phase.name, detail)
        _update_phase_state(wf, phase.name, "dry-run", detail)
        return "completed"
    logger.info("  [%s] %s — would call MCP tool (not executed)", phase.name, detail)
    _update_phase_state(wf, phase.name, "completed", detail)
    return "completed"


def _handle_agent(phase: Phase, context: dict[str, Any], wf: str, dry_run: bool) -> str:
    detail = f"AGENT invoke={phase.invoke}"
    if phase.action:
        detail += f"  action={phase.action}"
    if dry_run:
        logger.info("  [%s] (dry-run) %s", phase.name, detail)
        _update_phase_state(wf, phase.name, "dry-run", detail)
        return "completed"
    logger.info("  [%s] %s — would spawn agent (not executed)", phase.name, detail)
    _update_phase_state(wf, phase.name, "completed", detail)
    return "completed"


def _handle_interactive(phase: Phase, context: dict[str, Any], wf: str, dry_run: bool) -> str:
    detail = f"INTERACTIVE — requires user input: {phase.description}"
    if dry_run:
        logger.info("  [%s] (dry-run) %s", phase.name, detail)
        _update_phase_state(wf, phase.name, "dry-run", detail)
        return "paused"
    logger.info("  [%s] %s — execution paused", phase.name, detail)
    _update_phase_state(wf, phase.name, "paused", detail)
    return "paused"


def _handle_output(phase: Phase, context: dict[str, Any], wf: str, dry_run: bool) -> str:
    detail = f"OUTPUT format={phase.format or 'text'}"
    if dry_run:
        logger.info("  [%s] (dry-run) %s", phase.name, detail)
        _update_phase_state(wf, phase.name, "dry-run", detail)
        return "completed"
    logger.info("  [%s] %s — would render output (not executed)", phase.name, detail)
    _update_phase_state(wf, phase.name, "completed", detail)
    return "completed"


# ---------------------------------------------------------------------------
# Workflow execution
# ---------------------------------------------------------------------------

def execute_workflow(
    workflow: Workflow,
    context: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Run phases in sequence, respecting conditions and stop/skip logic.

    Returns a summary dict with per-phase statuses and overall result.
    """
    if context is None:
        context = {}

    mode = "DRY-RUN" if dry_run else "LIVE"
    logger.info(
        "=== Workflow: %s (%s) [%s] ===",
        workflow.name,
        workflow.version,
        mode,
    )
    logger.info("Description: %s", workflow.description)
    logger.info("Phases: %d", len(workflow.phases))
    if workflow.hooks:
        logger.info("Hooks: %s", ", ".join(workflow.hooks))
    logger.info("")

    results: dict[str, str] = {}
    overall = "completed"

    for phase in workflow.phases:
        try:
            status = _execute_phase(phase, context, workflow.name, dry_run)
        except Exception as exc:
            status = "error"
            logger.error("  [%s] ERROR: %s", phase.name, exc)
            _update_phase_state(workflow.name, phase.name, "error", str(exc))

        results[phase.name] = status

        if status == "stopped":
            overall = "stopped"
            logger.info("  >> Workflow stopped at phase '%s'", phase.name)
            break
        elif status == "paused":
            overall = "paused"
            logger.info("  >> Workflow paused at phase '%s' (interactive)", phase.name)
            break
        elif status == "error" and not phase.optional:
            overall = "error"
            logger.error("  >> Workflow aborted at phase '%s' (non-optional error)", phase.name)
            break

    logger.info("")
    logger.info("=== Result: %s ===", overall)

    return {
        "workflow": workflow.name,
        "version": workflow.version,
        "source": workflow.source_path,
        "mode": mode.lower(),
        "overall": overall,
        "phases": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_context_args(pairs: list[str] | None) -> dict[str, Any]:
    """Parse key=value pairs from CLI into a context dict."""
    ctx: dict[str, Any] = {}
    if not pairs:
        return ctx
    for pair in pairs:
        if "=" not in pair:
            ctx[pair] = True
        else:
            key, value = pair.split("=", 1)
            ctx[key.strip()] = value.strip()
    return ctx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Datacore Workflow Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("workflow", help="Path to workflow YAML file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without executing",
    )
    parser.add_argument(
        "--context",
        nargs="*",
        metavar="KEY=VALUE",
        help="Context key=value pairs for condition evaluation",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    try:
        workflow = load_workflow(args.workflow)
    except WorkflowError as exc:
        logger.error("Failed to load workflow: %s", exc)
        sys.exit(1)

    context = _parse_context_args(args.context)
    result = execute_workflow(workflow, context=context, dry_run=args.dry_run)

    # Print summary
    print()
    print(yaml.safe_dump(result, default_flow_style=False, sort_keys=False))


if __name__ == "__main__":
    main()
