#!/usr/bin/env python3
"""Venture framework hooks for nightshift integration.

Post-execution hook: updates cadence-log.yaml when a venture task completes.
Pre-run hook: runs cadence_runner to generate tasks before nightshift processes them.
"""

import sys
from datetime import date
from pathlib import Path

# Ensure module imports work
_module_root = Path(__file__).resolve().parent.parent
if str(_module_root.parent) not in sys.path:
    sys.path.insert(0, str(_module_root.parent))


def on_task_complete(task_properties: dict, space_dir: Path, success: bool = True):
    """Post-execution hook: update cadence-log.yaml when a venture cadence task completes.

    Called after nightshift successfully executes a :AI:venture: task.

    Args:
        task_properties: dict with VENTURE, ROLE, CADENCE, FREQUENCY keys
        space_dir: Path to the venture space (e.g., ~/Data/3-fds)
        success: whether the task executed successfully
    """
    from ventures.lib.cadence_engine import load_cadence_log, save_cadence_log

    venture = task_properties.get("VENTURE", "")
    role = task_properties.get("ROLE", "")
    cadence = task_properties.get("CADENCE", "")

    if not all([venture, role, cadence]):
        return  # Not a venture cadence task

    cadence_log_path = space_dir / ".datacore" / "state" / "venture" / "cadence-log.yaml"
    log = load_cadence_log(cadence_log_path)

    log_key = f"{role}.{cadence}"
    log[log_key] = {
        "last_run": date.today().isoformat(),
        "result": "ok" if success else "failed",
    }

    save_cadence_log(log, cadence_log_path)


def heartbeat(data_dir: Path, dry_run: bool = False) -> dict:
    """Heartbeat: check all ventures for overdue cadences and generate tasks.

    Call this at the start of any agent session (nightshift run, manual, hermes).
    It's idempotent — won't create duplicate tasks for cadences that already
    have pending :AI:venture: tasks in the org file.

    Returns summary dict.
    """
    import yaml
    from ventures.lib.venture_discovery import discover_ventures
    from ventures.lib.cadence_engine import (
        find_overdue_cadences,
        load_cadence_log,
        save_cadence_log,
        generate_rich_cadence_task,
    )
    from ventures.lib.budget_tracker import load_ledger

    today = date.today()
    ventures = discover_ventures(data_dir, nightshift_only=True)
    results = []

    for vs in ventures:
        # Load roles from raw YAML (cadence engine expects dict format)
        venture_file = vs.space_dir / "venture.yaml"
        with open(venture_file) as f:
            raw = yaml.safe_load(f)
        roles_raw = raw.get("roles", {})
        if not isinstance(roles_raw, dict):
            continue

        # Find overdue
        cadence_log_path = vs.space_dir / ".datacore" / "state" / "venture" / "cadence-log.yaml"
        cadence_log = load_cadence_log(cadence_log_path)
        overdue = find_overdue_cadences(roles_raw, cadence_log, today)

        if not overdue:
            results.append({"venture": vs.config.name, "new_tasks": 0})
            continue

        # Dedup: check which cadences already have pending tasks in org file
        org_file = vs.space_dir / "org" / "next_actions.org"
        existing_cadences = set()
        if org_file.exists():
            content = org_file.read_text()
            for line in content.split("\n"):
                if ":AI:venture:" in line and "TODO" in line:
                    # Extract cadence name from heading pattern: [venture] role: cadence-name
                    parts = line.split(":")
                    for part in parts:
                        part = part.strip()
                        if part and not part.startswith("AI") and not part.startswith("venture"):
                            # Try to match "role: cadence" from heading
                            pass
                    # Simpler: check CADENCE property in following lines
                    pass

            # Better approach: scan for CADENCE properties
            import re
            for match in re.finditer(r':CADENCE:\s*(.+)', content):
                existing_cadences.add(match.group(1).strip())

        # Filter out cadences that already have pending tasks
        new_overdue = [c for c in overdue if c.cadence_name not in existing_cadences]

        if not new_overdue:
            results.append({"venture": vs.config.name, "new_tasks": 0, "already_queued": len(overdue)})
            continue

        if dry_run:
            results.append({"venture": vs.config.name, "new_tasks": len(new_overdue), "dry_run": True})
            continue

        # Write tasks — append raw org text (no org_workspace dependency)
        written = 0
        with open(org_file, "a") as f:
            for entry in new_overdue:
                task = generate_rich_cadence_task(entry, vs.config.name, str(vs.space_dir))
                heading = task["heading"]
                tags = task["tags_str"]
                props = task["properties"]

                f.write(f"\n** TODO {heading}    {tags}\n")
                f.write(":PROPERTIES:\n")
                for k, v in props.items():
                    # Multiline values: use | continuation
                    if "\n" in str(v):
                        lines = str(v).split("\n")
                        f.write(f":{k}: {lines[0]}\n")
                        for cont_line in lines[1:]:
                            f.write(f":  | {cont_line}\n")
                    else:
                        f.write(f":{k}: {v}\n")
                f.write(":END:\n")
                written += 1

        results.append({"venture": vs.config.name, "new_tasks": written})

    return {
        "date": today.isoformat(),
        "dry_run": dry_run,
        "ventures": results,
    }
