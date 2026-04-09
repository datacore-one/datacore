#!/usr/bin/env python3
"""cadence_runner.py — CLI entry point for nightshift cadence processing.

Discovers ventures, finds overdue cadences, filters by budget, generates
rich org tasks, and writes them to each venture's next_actions.org.

Usage:
    python3 cadence_runner.py [--data-dir=PATH] [--dry-run] [--venture=NAME]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_to_python_path(lib_dir: Path) -> None:
    """Ensure a directory is on sys.path for imports."""
    s = str(lib_dir)
    if s not in sys.path:
        sys.path.insert(0, s)


def _load_roles_raw(venture_yaml: Path) -> dict | None:
    """Load roles dict from venture.yaml raw YAML.

    Returns the roles dict if it's a dict (new format), None if it's a list
    (old format) or missing.
    """
    with open(venture_yaml) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return None
    roles = data.get("roles")
    if not isinstance(roles, dict):
        return None
    return roles


def _write_task_via_adapter(
    adapter_path: Path,
    org_file: Path,
    task: dict,
) -> bool:
    """Write a task to an org file using org_workspace_adapter.py.

    Falls back to raw org text append if the adapter fails.
    Returns True on success.
    """
    heading = task["heading"]
    tags = task["tags_str"]
    state = task["state"]

    # Try subprocess call to adapter
    cmd = [
        sys.executable,
        str(adapter_path),
        "add",
        f"--file={org_file}",
        f"--heading={heading}",
        f"--state={state}",
        f"--tags={tags}",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            # Task created — now try to set properties via org_workspace
            _set_properties_via_workspace(org_file, heading, task["properties"])
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: append raw org text
    _append_raw_org(org_file, task)
    return True


def _set_properties_via_workspace(
    org_file: Path,
    heading: str,
    properties: dict,
) -> None:
    """Set properties on a task via org_workspace (best-effort)."""
    try:
        from org_workspace import OrgWorkspace

        ws = OrgWorkspace()
        ws.load(org_file)
        for node in ws.all_nodes():
            if node.heading == heading:
                for key, value in properties.items():
                    node.set_property(key, str(value))
                ws.save()
                return
    except Exception:
        pass  # Properties are nice-to-have; task heading is enough


def _append_raw_org(org_file: Path, task: dict) -> None:
    """Append a task as raw org-mode text (fallback)."""
    org_file.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"\n** {task['state']} {task['heading']} {task['tags_str']}"]
    props = task.get("properties", {})
    if props:
        lines.append(":PROPERTIES:")
        for key, value in props.items():
            lines.append(f":{key}: {value}")
        lines.append(":END:")

    with open(org_file, "a") as f:
        f.write("\n".join(lines) + "\n")


def _update_cadence_log(
    cadence_log: dict,
    entries: list,
    today: date,
) -> dict:
    """Update cadence log with today's date for queued entries."""
    today_str = today.isoformat()
    for entry in entries:
        role = entry.role
        freq = entry.frequency
        name = entry.cadence_name
        cadence_log.setdefault(role, {}).setdefault(freq, {})[name] = today_str
    return cadence_log


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run(
    data_dir: Path,
    dry_run: bool = False,
    venture_filter: str | None = None,
) -> dict:
    """Run cadence processing for all eligible ventures.

    Returns a summary dict with results per venture.
    """
    # Ensure lib dirs are importable
    ventures_lib = Path(__file__).parent
    _add_to_python_path(ventures_lib)
    _add_to_python_path(data_dir / ".datacore" / "lib")

    from venture_discovery import discover_ventures
    from cadence_engine import (
        find_overdue_cadences,
        filter_by_budget,
        generate_rich_cadence_task,
        load_cadence_log,
        save_cadence_log,
    )
    from budget_tracker import load_ledger

    today = date.today()
    adapter_path = data_dir / ".datacore" / "lib" / "org_workspace_adapter.py"

    # Discover ventures
    ventures = discover_ventures(data_dir, nightshift_only=True)

    # Apply venture name filter
    if venture_filter:
        ventures = [v for v in ventures if venture_filter.lower() in v.name.lower()]

    summary = {
        "date": today.isoformat(),
        "dry_run": dry_run,
        "ventures": [],
    }

    for vs in ventures:
        venture_result = {
            "name": vs.name,
            "space_dir": str(vs.space_dir),
            "overdue": 0,
            "executable": 0,
            "skipped": 0,
            "tasks_written": 0,
            "errors": [],
        }

        # Load roles from raw YAML (dict format required)
        venture_yaml = vs.space_dir / "venture.yaml"
        roles = _load_roles_raw(venture_yaml)
        if roles is None:
            venture_result["errors"].append(
                "Roles not in dict format or missing — skipping cadence processing"
            )
            summary["ventures"].append(venture_result)
            continue

        # Load cadence log
        cadence_log_path = vs.space_dir / ".datacore" / "cadence-log.yaml"
        cadence_log = load_cadence_log(cadence_log_path)

        # Find overdue cadences
        overdue = find_overdue_cadences(roles, cadence_log, today=today)
        venture_result["overdue"] = len(overdue)

        if not overdue:
            summary["ventures"].append(venture_result)
            continue

        # Budget filtering
        budget_ledger_path = vs.space_dir / ".datacore" / "budget-ledger.yaml"
        ledger = load_ledger(budget_ledger_path)
        ceiling = vs.config.budget.ceiling
        ai_ceiling = vs.config.budget.ai_tokens
        real_ceiling = vs.config.budget.real_spend

        executable, skipped = filter_by_budget(
            overdue, ledger, ceiling, ai_ceiling, real_ceiling
        )
        venture_result["executable"] = len(executable)
        venture_result["skipped"] = len(skipped)

        if not executable:
            summary["ventures"].append(venture_result)
            continue

        # Generate rich tasks and write to org file
        org_file = vs.space_dir / "org" / "next_actions.org"
        tasks_written = 0

        for entry in executable:
            task = generate_rich_cadence_task(
                entry,
                venture_name=vs.config.name,
                venture_dir=vs.space_dir,
            )

            if dry_run:
                tasks_written += 1
                continue

            try:
                _write_task_via_adapter(adapter_path, org_file, task)
                tasks_written += 1
            except Exception as exc:
                venture_result["errors"].append(
                    f"Failed to write task '{entry.cadence_name}': {exc}"
                )

        venture_result["tasks_written"] = tasks_written

        # Update cadence log (mark queued cadences as run today)
        if not dry_run and tasks_written > 0:
            cadence_log = _update_cadence_log(cadence_log, executable, today)
            save_cadence_log(cadence_log, cadence_log_path)

        summary["ventures"].append(venture_result)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Process overdue venture cadences for nightshift execution."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / "Data",
        help="Root Datacore data directory (default: ~/Data)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing tasks or updating logs",
    )
    parser.add_argument(
        "--venture",
        type=str,
        default=None,
        help="Filter to a specific venture name (substring match)",
    )

    args = parser.parse_args()
    summary = run(
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        venture_filter=args.venture,
    )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
