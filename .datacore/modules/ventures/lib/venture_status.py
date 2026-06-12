#!/usr/bin/env python3
"""Generate venture portfolio status for /today hook."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

# Add module path so sibling imports work
_module_root = Path(__file__).resolve().parent.parent
if str(_module_root.parent) not in sys.path:
    sys.path.insert(0, str(_module_root.parent))

from ventures.lib.venture_discovery import discover_ventures
from ventures.lib.cadence_engine import (
    cadence_log_path_for,
    find_overdue_cadences,
    load_cadence_log_safe,
)
from ventures.lib.budget_tracker import load_ledger, get_remaining
from ventures.lib.hypothesis_tracker import load_hypotheses_file, summary as hyp_summary


def _load_roles_dict(venture_yaml: Path) -> dict:
    """Load the raw roles section from venture.yaml as the dict format cadence_engine expects.

    venture.yaml stores roles as a list:
        roles:
          - id: operator
            cadences:
              daily: [check-etsy-stats]

    cadence_engine.find_overdue_cadences expects:
        {"operator": {"cadences": {"daily": ["check-etsy-stats"]}}}
    """
    if not venture_yaml.exists():
        return {}
    with open(venture_yaml) as f:
        data = yaml.safe_load(f)
    raw_roles = data.get("roles", [])
    if isinstance(raw_roles, dict):
        # Already in dict format — pass through
        return raw_roles
    if isinstance(raw_roles, list):
        result = {}
        for role in raw_roles:
            role_id = role.get("id", "")
            if role_id:
                # Preserve cadences sub-key if present; cadence_engine reads role_data["cadences"]
                result[role_id] = {"cadences": role.get("cadences", {})}
        return result
    return {}


def _collect_venture_data(vs, today: date) -> dict:
    """Collect all status data for a single VentureSpace."""
    cfg = vs.config

    # --- Cadences --- (canonical path with legacy migration — audit A8)
    cadence_log_path = cadence_log_path_for(vs.space_dir)
    cadence_log = load_cadence_log_safe(cadence_log_path)
    roles_dict = _load_roles_dict(vs.space_dir / "venture.yaml")
    overdue = find_overdue_cadences(roles_dict, cadence_log, today=today)

    # --- Budget ---
    budget_ledger_path = vs.space_dir / ".datacore" / "state" / "venture" / "budget-ledger.yaml"
    ledger = load_ledger(budget_ledger_path)
    remaining = get_remaining(
        ledger,
        monthly_ceiling=cfg.budget.ceiling,
        ai_ceiling=cfg.budget.ai_tokens,
        real_ceiling=cfg.budget.real_spend,
    )

    # --- Hypotheses ---
    hyp_path = vs.space_dir / "hypotheses.yaml"
    hyp_data = None
    if hyp_path.exists():
        try:
            board = load_hypotheses_file(hyp_path)
            hyp_data = hyp_summary(board)
        except Exception:
            hyp_data = None

    return {
        "name": cfg.name,
        "stage": cfg.stage,
        "autonomy": cfg.autonomy,
        "overdue_cadences": overdue,
        "budget": {
            "remaining": remaining["total"],
            "ceiling": cfg.budget.ceiling,
        },
        "hypotheses": hyp_data,
    }


def _format_markdown(ventures_data: list[dict]) -> str:
    if not ventures_data:
        return "No ventures found."

    lines = ["## Ventures", ""]
    for vd in ventures_data:
        name = vd["name"].capitalize()
        stage = vd["stage"]
        autonomy = vd["autonomy"]
        overdue = vd["overdue_cadences"]
        budget = vd["budget"]
        hyp = vd["hypotheses"]

        # Header line
        lines.append(f"### {name} ({stage}) — Autonomy {autonomy}")

        # Summary line
        overdue_count = len(overdue)
        budget_remaining = budget["remaining"]
        budget_ceiling = budget["ceiling"]

        parts = [f"Cadences: {overdue_count} overdue"]
        parts.append(f"Budget: ${budget_remaining:.0f}/${budget_ceiling:.0f}")
        if hyp is not None:
            hyp_parts = []
            if hyp.get("active", 0):
                hyp_parts.append(f"{hyp['active']} active")
            if hyp.get("backlog", 0):
                hyp_parts.append(f"{hyp['backlog']} backlog")
            if not hyp_parts:
                hyp_parts.append("0 active")
            parts.append(f"Hypotheses: {', '.join(hyp_parts)}")
        else:
            parts.append("Hypotheses: none")

        lines.append(" | ".join(parts))

        if overdue:
            for entry in overdue:
                days = entry.days_overdue
                lines.append(f"- {entry.role}: {entry.cadence_name} ({days}d overdue)")
        else:
            lines.append("All cadences current.")

        lines.append("")

    # Strip trailing blank line
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def _format_json(ventures_data: list[dict]) -> str:
    output = []
    for vd in ventures_data:
        hyp = vd["hypotheses"] or {}
        output.append({
            "name": vd["name"],
            "stage": vd["stage"],
            "autonomy": vd["autonomy"],
            "overdue_cadences": len(vd["overdue_cadences"]),
            "budget_remaining": round(vd["budget"]["remaining"], 2),
            "budget_ceiling": vd["budget"]["ceiling"],
            "hypotheses": {
                "active": hyp.get("active", 0),
                "backlog": hyp.get("backlog", 0),
            },
        })
    return json.dumps({"ventures": output})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate venture portfolio status for /today hook."
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / "Data"),
        help="Path to the Datacore data directory (default: ~/Data)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    today = date.today()

    ventures = discover_ventures(data_dir)

    if not ventures:
        if args.format == "json":
            print(json.dumps({"ventures": []}))
        else:
            print("No ventures found.")
        return

    ventures_data = [_collect_venture_data(vs, today) for vs in ventures]

    if args.format == "json":
        print(_format_json(ventures_data))
    else:
        print(_format_markdown(ventures_data))


if __name__ == "__main__":
    main()
