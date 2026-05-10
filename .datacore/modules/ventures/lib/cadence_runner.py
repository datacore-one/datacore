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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Heartbeat self-report — skip list
# ---------------------------------------------------------------------------
# Ventures whose heartbeat.json must NOT be written by this runner.
# The desktop app's Firm Status panel labels these "monitored separately"
# and expects no heartbeat.json present. Per parallel-session contract.
HEARTBEAT_SKIP_VENTURES = frozenset({"6-meridian"})


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

    Properties are MANDATORY for nightshift execution (CONTEXT and
    ACCEPTANCE_CRITERIA are checked by the executor — without them tasks
    get rejected with category=specification, retryable=false). They are
    passed through the adapter as --property KEY=VALUE so the heading,
    body, and properties land in one atomic call.

    Falls back to raw org text append if the adapter call fails (e.g.,
    when the org file has parse errors and adapter cannot load it).

    Returns True on success.
    """
    heading = task["heading"]
    tags = task["tags_str"]
    state = task["state"]
    properties = task.get("properties", {})
    body = task.get("body")

    # Build adapter command with properties baked in
    cmd = [
        sys.executable,
        str(adapter_path),
        "add",
        f"--file={org_file}",
        f"--heading={heading}",
        f"--state={state}",
        f"--tags={tags}",
    ]
    for key, value in properties.items():
        # Adapter accepts repeatable --property KEY=VALUE
        cmd.append(f"--property={key}={value}")
    if body:
        cmd.append(f"--body={body}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: append raw org text (preserves all properties)
    _append_raw_org(org_file, task)
    return True


def _append_raw_org(org_file: Path, task: dict) -> None:
    """Append a task as raw org-mode text (fallback when adapter fails).

    Drawer + property indentation matches what org-workspace produces
    (2-space indent under the heading) so emacs `org-agenda` parses
    properties as belonging to the task. Earlier versions wrote
    properties at column 0 — that broke drawer pairing and was the
    original source of the leaked-property corruption (ENG-2026-0504-025).
    """
    org_file.parent.mkdir(parents=True, exist_ok=True)

    indent = "  "
    lines = [f"\n** {task['state']} {task['heading']} {task['tags_str']}"]
    props = task.get("properties", {})
    if props:
        lines.append(f"{indent}:PROPERTIES:")
        for key, value in props.items():
            lines.append(f"{indent}:{key}: {value}")
        lines.append(f"{indent}:END:")

    body = task.get("body")
    if body:
        for body_line in body.split("\n"):
            lines.append(f"{indent}{body_line}")

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_next_due(roles: dict, cadence_log: dict, today: date) -> str | None:
    """Soonest upcoming cadence across all roles. Returns ISO Z timestamp.

    For each cadence in the venture's roles, compute when it next becomes due
    (last_run + frequency_window). If never run, treat as due now. Pick the
    earliest such timestamp.
    """
    freq_days = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90}
    earliest: date | None = None

    for role_id, role_data in roles.items():
        cadences = role_data.get("cadences", {}) or {}
        role_log = cadence_log.get(role_id, {}) or {}
        for frequency, names in cadences.items():
            window = freq_days.get(frequency)
            if window is None:
                continue
            freq_log = role_log.get(frequency, {}) or {}
            names_list = names if isinstance(names, list) else []
            for name in names_list:
                last_run_str = freq_log.get(name)
                if last_run_str:
                    try:
                        last_run = date.fromisoformat(str(last_run_str))
                        next_due = last_run + timedelta(days=window)
                    except ValueError:
                        next_due = today
                else:
                    next_due = today  # never run → due now
                if earliest is None or next_due < earliest:
                    earliest = next_due

    if earliest is None:
        return None
    # 09:00 UTC is when the FDS daily cadence has historically fired; use that
    # as a reasonable hour-of-day for the dashboard's overdue countdown.
    return earliest.strftime("%Y-%m-%dT09:00:00Z")


def _count_24h_fires(cadence_log: dict, today: date) -> int:
    """Count entries in the cadence log dated within the last 24h.

    Accepts the nested format {role: {frequency: {cadence: 'YYYY-MM-DD'}}}.
    """
    cutoff = today - timedelta(days=1)
    count = 0
    for role_data in cadence_log.values():
        if not isinstance(role_data, dict):
            continue
        for freq_data in role_data.values():
            if not isinstance(freq_data, dict):
                continue
            for date_str in freq_data.values():
                try:
                    if date.fromisoformat(str(date_str)) > cutoff:
                        count += 1
                except (ValueError, TypeError):
                    pass
    return count


def _write_heartbeat_json(
    space_dir: Path,
    venture_name: str,
    last_status: str,
    last_error: str | None,
    next_due: str | None,
    cadences_fired_24h: int,
    cadences_overdue: int,
) -> Path:
    """Write the per-venture heartbeat.json under the contract the desktop app reads.

    Schema (verbatim, the daemon parses these field names):
        venture, last_fire, last_status, last_error, next_due,
        cadences_fired_24h, cadences_overdue, decisions_pending[]
    """
    target = space_dir / ".datacore" / "state" / "heartbeat.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "venture": venture_name,
        "last_fire": _now_iso(),
        "last_status": last_status,
        "last_error": last_error,
        "next_due": next_due,
        "cadences_fired_24h": cadences_fired_24h,
        "cadences_overdue": cadences_overdue,
        "decisions_pending": [],
    }

    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(target)
    return target


def _seed_auto_defaults_if_missing(space_dir: Path) -> None:
    """Create an empty auto-defaults.yaml if the venture lacks one.

    Empty `defaults: []` is valid per the dashboard contract — the venture
    simply has no auto-resolution rules yet. Real rules can land later.
    """
    target = space_dir / ".datacore" / "policies" / "auto-defaults.yaml"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# auto-defaults.yaml — venture decision policies\n"
        "# Empty defaults means no auto-resolution rules yet.\n"
        "# See ~/Data/.datacore/templates/auto-defaults.example.yaml for a fuller template.\n"
        "defaults: []\n",
        encoding="utf-8",
    )


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

    # Best-effort live agent stream emitter (.datacore/lib was added above).
    try:
        from agent_emit import emit as _emit
    except Exception:
        def _emit(*_a, **_kw):
            return {}

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

    def _finalize(
        venture_result: dict,
        space_dir: Path,
        venture_name: str,
        last_status: str,
        last_error: str | None = None,
        roles: dict | None = None,
        cadence_log: dict | None = None,
    ) -> None:
        """Write per-venture heartbeat.json + seed auto-defaults.yaml, then
        append to summary. Skipped for ventures in HEARTBEAT_SKIP_VENTURES
        (e.g. 6-meridian — monitored separately) and during dry-run.
        """
        if not dry_run and venture_name not in HEARTBEAT_SKIP_VENTURES:
            try:
                fired_24h = _count_24h_fires(cadence_log or {}, today)
                next_due = (
                    _compute_next_due(roles, cadence_log or {}, today)
                    if roles is not None
                    else None
                )
                _write_heartbeat_json(
                    space_dir=space_dir,
                    venture_name=venture_name,
                    last_status=last_status,
                    last_error=last_error,
                    next_due=next_due,
                    cadences_fired_24h=fired_24h,
                    cadences_overdue=venture_result.get("overdue", 0),
                )
                _seed_auto_defaults_if_missing(space_dir)
            except Exception as exc:
                venture_result.setdefault("errors", []).append(
                    f"Heartbeat write failed: {exc}"
                )
        summary["ventures"].append(venture_result)

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

        # Skip archived ventures (Phase A.0.2 state-machine gate). Archived
        # ventures keep their venture.yaml but must not generate new cadence
        # tasks — restoring transitions stage back to discovery. No heartbeat
        # write either (the venture is intentionally inert).
        venture_stage = (vs.config.stage if vs.config else "").lower()
        if venture_stage == "archived":
            venture_result["skipped_reason"] = "stage=archived"
            summary["ventures"].append(venture_result)
            continue

        # Load roles from raw YAML (dict format required)
        venture_yaml = vs.space_dir / "venture.yaml"
        roles = _load_roles_raw(venture_yaml)
        if roles is None:
            venture_result["errors"].append(
                "Roles not in dict format or missing — skipping cadence processing"
            )
            _finalize(
                venture_result,
                vs.space_dir,
                vs.name,
                last_status="error",
                last_error=venture_result["errors"][0],
            )
            continue

        # Load cadence log. Canonical path is .datacore/state/venture/
        # cadence-log.yaml (matches venture_heartbeat.py). The legacy path
        # at .datacore/cadence-log.yaml is read as fallback for installs
        # that haven't migrated yet.
        cadence_log_path = vs.space_dir / ".datacore" / "state" / "venture" / "cadence-log.yaml"
        legacy_log_path = vs.space_dir / ".datacore" / "cadence-log.yaml"
        if cadence_log_path.exists():
            try:
                cadence_log = load_cadence_log(cadence_log_path)
            except Exception:
                cadence_log = {}  # malformed YAML — heartbeat will surface as error
        elif legacy_log_path.exists():
            cadence_log = load_cadence_log(legacy_log_path)
        else:
            cadence_log = {}

        # Find overdue cadences
        overdue = find_overdue_cadences(roles, cadence_log, today=today)
        venture_result["overdue"] = len(overdue)

        if not overdue:
            # Healthy: nothing overdue, nothing to fire.
            _finalize(
                venture_result,
                vs.space_dir,
                vs.name,
                last_status="ok",
                roles=roles,
                cadence_log=cadence_log,
            )
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
            # Budget-blocked: there is overdue work but no AI budget to spend
            # on it. Surface as "blocked" so the dashboard renders red.
            _finalize(
                venture_result,
                vs.space_dir,
                vs.name,
                last_status="blocked",
                last_error="Budget exhausted — only daily cadences eligible and none overdue",
                roles=roles,
                cadence_log=cadence_log,
            )
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

        # Stream a single per-venture summary so the agent feed shows
        # cadence activity (one line per venture per run, not per task).
        if not dry_run and tasks_written > 0:
            cadence_names = ", ".join(e.cadence_name for e in executable[:3])
            if len(executable) > 3:
                cadence_names += f", +{len(executable) - 3} more"
            _emit(
                "ventures.cadences.queued",
                agent=vs.name,
                summary=(f"Queued {tasks_written} cadence task(s) for {vs.name}"
                         f": {cadence_names}"),
                severity="success",
                details={
                    "venture": vs.name,
                    "tasks_written": tasks_written,
                    "overdue": len(overdue),
                    "skipped": len(skipped),
                    "stage": venture_stage or None,
                },
            )

        # Normal end of per-venture path — write heartbeat and append.
        # If any task-write errors occurred, surface as "error"; otherwise
        # the venture is healthy.
        if venture_result.get("errors"):
            _finalize(
                venture_result,
                vs.space_dir,
                vs.name,
                last_status="error",
                last_error=venture_result["errors"][0],
                roles=roles,
                cadence_log=cadence_log,
            )
        else:
            _finalize(
                venture_result,
                vs.space_dir,
                vs.name,
                last_status="ok",
                roles=roles,
                cadence_log=cadence_log,
            )

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
