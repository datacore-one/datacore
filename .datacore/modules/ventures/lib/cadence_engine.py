"""
cadence_engine.py — Identify overdue venture cadences and generate org tasks.

Reads role cadences from a venture's roles dict (matching venture.yaml format),
checks a cadence-log.yaml for last run times, and returns what's overdue.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml


FREQUENCY_WINDOWS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
}

# Priority order: daily tasks are most urgent
FREQUENCY_PRIORITY = ["daily", "weekly", "monthly", "quarterly"]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class CadenceEntry:
    """A single overdue cadence item."""

    role: str
    cadence_name: str
    frequency: str
    days_overdue: int = 0


# ---------------------------------------------------------------------------
# Log I/O
# ---------------------------------------------------------------------------


def load_cadence_log(path: Path) -> dict:
    """Load a cadence log YAML file. Returns empty dict if file is missing."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def save_cadence_log(log: dict, path: Path) -> None:
    """Save a cadence log dict to YAML, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(log, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def find_overdue_cadences(
    roles: dict,
    cadence_log: dict,
    today: Optional[date] = None,
) -> list:
    """Find all overdue cadences across all roles.

    A cadence is overdue if:
    - It has never run (not in cadence_log), OR
    - today - last_run >= frequency_window

    Returns a list of CadenceEntry sorted by:
    1. Frequency priority (daily first, then weekly, monthly, quarterly)
    2. Days overdue descending (most overdue first within same frequency)

    Args:
        roles: Role dict from venture.yaml (keyed by role id, each with "cadences" sub-dict).
        cadence_log: Nested dict: {role: {frequency: {cadence_name: "YYYY-MM-DD"}}}.
        today: Date to use as "today". Defaults to date.today().
    """
    if today is None:
        today = date.today()

    overdue: list[CadenceEntry] = []

    for role_id, role_data in roles.items():
        cadences = role_data.get("cadences", {})
        role_log = cadence_log.get(role_id, {})

        for frequency, cadence_names in cadences.items():
            window = FREQUENCY_WINDOWS.get(frequency)
            if window is None:
                # Unknown frequency — skip
                continue

            freq_log = role_log.get(frequency, {})

            for name in cadence_names:
                last_run_str = freq_log.get(name)

                if last_run_str is None:
                    # Never run — treat as maximally overdue
                    overdue.append(
                        CadenceEntry(
                            role=role_id,
                            cadence_name=name,
                            frequency=frequency,
                            days_overdue=window.days,
                        )
                    )
                else:
                    last_run = date.fromisoformat(str(last_run_str))
                    delta = (today - last_run).days
                    if delta >= window.days:
                        overdue.append(
                            CadenceEntry(
                                role=role_id,
                                cadence_name=name,
                                frequency=frequency,
                                days_overdue=delta,
                            )
                        )

    # Sort by frequency priority, then by days_overdue descending
    priority_index = {freq: i for i, freq in enumerate(FREQUENCY_PRIORITY)}

    overdue.sort(
        key=lambda e: (
            priority_index.get(e.frequency, len(FREQUENCY_PRIORITY)),
            -e.days_overdue,
        )
    )

    return overdue


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------


def generate_cadence_task(entry: CadenceEntry, venture_name: str) -> dict:
    """Generate a simple org task dict for an overdue cadence entry.

    Returns a dict with:
        heading   — task title
        state     — "TODO"
        tags_str  — org-mode tag string e.g. ":AI:venture:cmo:"
        properties — dict with ROLE, VENTURE, CADENCE, FREQUENCY, DAYS_OVERDUE
    """
    heading = f"[{venture_name}] {entry.cadence_name} ({entry.frequency})"
    tags_str = f":AI:venture:{entry.role}:"

    return {
        "heading": heading,
        "state": "TODO",
        "tags_str": tags_str,
        "properties": {
            "ROLE": entry.role,
            "VENTURE": venture_name,
            "CADENCE": entry.cadence_name,
            "FREQUENCY": entry.frequency,
            "DAYS_OVERDUE": entry.days_overdue,
        },
    }


# ---------------------------------------------------------------------------
# Budget-aware filtering
# ---------------------------------------------------------------------------


def filter_by_budget(
    overdue: list,
    ledger,
    monthly_ceiling: float,
    ai_ceiling: float,
    real_ceiling: float,
) -> tuple:
    """Filter overdue cadences based on remaining budget.

    If AI budget remaining <= 0, only daily cadences are kept (they're essential).
    Otherwise all overdue cadences pass through.

    Returns (executable, skipped) — both lists of CadenceEntry.
    """
    from budget_tracker import get_remaining

    remaining = get_remaining(ledger, monthly_ceiling, ai_ceiling, real_ceiling)
    ai_remaining = remaining["ai"]

    if ai_remaining <= 0:
        executable = [e for e in overdue if e.frequency == "daily"]
        skipped = [e for e in overdue if e.frequency != "daily"]
    else:
        executable = list(overdue)
        skipped = []

    return executable, skipped


# ---------------------------------------------------------------------------
# Rich task generation (nightshift)
# ---------------------------------------------------------------------------


def generate_rich_cadence_task(
    entry: CadenceEntry,
    venture_name: str,
    venture_dir=None,
) -> dict:
    """Generate an enriched org task dict for nightshift execution.

    Like generate_cadence_task but adds CONTEXT, BOOTSTRAP, EFFORT,
    ACCEPTANCE_CRITERIA, and TOOLS properties that give nightshift agents
    richer context for autonomous execution.

    Args:
        entry: The overdue cadence entry.
        venture_name: Human-readable venture name.
        venture_dir: Path to the venture's space directory (optional).
    """
    base = generate_cadence_task(entry, venture_name)

    # Build context string pointing to venture space
    context_parts = [f"Venture: {venture_name}"]
    if venture_dir is not None:
        context_parts.append(f"Space: {venture_dir}")
        context_parts.append(f"Config: {venture_dir}/venture.yaml")
    context = " | ".join(context_parts)

    # Bootstrap: instructions for the nightshift agent to orient
    bootstrap = (
        f"Read venture.yaml and role '{entry.role}' definition. "
        f"Check previous cadence results in .datacore/cadence-log.yaml. "
        f"Execute '{entry.cadence_name}' for the {entry.role} role."
    )

    # Effort estimate based on frequency
    effort_map = {"daily": "15min", "weekly": "30min", "monthly": "1h", "quarterly": "2h"}
    effort = effort_map.get(entry.frequency, "30min")

    # Acceptance criteria
    acceptance = (
        f"Cadence '{entry.cadence_name}' completed successfully. "
        f"Results logged to cadence-log.yaml with status and summary."
    )

    # Suggested tools
    tools = "plur_recall_hybrid, datacore.search, Read, Grep, Glob"

    base["properties"].update(
        {
            "CONTEXT": context,
            "BOOTSTRAP": bootstrap,
            "EFFORT": effort,
            "ACCEPTANCE_CRITERIA": acceptance,
            "TOOLS": tools,
        }
    )

    return base
