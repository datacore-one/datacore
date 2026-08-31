"""
cadence_engine.py — Identify overdue venture cadences and generate org tasks.

Reads role cadences from a venture's roles dict (matching venture.yaml format),
checks a cadence-log.yaml for last run times, and returns what's overdue.
"""

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml


logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "cadences"

# Ventures whose heartbeat.json must NOT be written by the heartbeat or the
# cadence runner. The desktop app's Firm Status panel labels these
# "monitored separately" and expects no heartbeat.json present.
# Single definition (audit A9) — imported by cadence_runner.py and
# venture_heartbeat.py.
HEARTBEAT_SKIP_VENTURES = frozenset({"6-meridian"})

# org-mode states meaning a task is finished. Tasks in these states must
# NOT suppress cadence re-fires (audit A4).
COMPLETED_TASK_STATES = frozenset({"DONE", "CANCELLED", "CANCELED", "KILLED"})


def load_cadence_template(cadence_name: str, templates_dir: Path = None) -> Optional[str]:
    """Load a cadence template markdown file, stripping YAML frontmatter.
    Returns the template body text, or None if no template exists.
    """
    if templates_dir is None:
        templates_dir = TEMPLATES_DIR
    template_path = templates_dir / f"{cadence_name}.md"
    if not template_path.exists():
        return None
    content = template_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    if lines and lines[0].strip() == "---":
        end_idx = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx is not None:
            content = "\n".join(lines[end_idx + 1:]).strip()
    return content


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


def cadence_log_path_for(space_dir: Path) -> Path:
    """Resolve the canonical cadence-log path for a venture space.

    Canonical: [space]/.datacore/state/venture/cadence-log.yaml
    Legacy:    [space]/.datacore/cadence-log.yaml

    Migrate-on-read: if the canonical file is missing but the legacy one
    exists, the legacy log is copied to the canonical location so all
    readers and writers converge on one path (audit A8 — forge's log lived
    at the legacy path only, making every cadence look overdue every tick).
    """
    space_dir = Path(space_dir)
    canonical = space_dir / ".datacore" / "state" / "venture" / "cadence-log.yaml"
    legacy = space_dir / ".datacore" / "cadence-log.yaml"
    if not canonical.exists() and legacy.exists():
        try:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, canonical)
            logger.warning(
                "Migrated legacy cadence log %s -> %s", legacy, canonical
            )
        except OSError as exc:
            logger.error(
                "Failed to migrate legacy cadence log %s -> %s: %s",
                legacy, canonical, exc,
            )
            return legacy
    return canonical


def load_cadence_log_safe(path: Path) -> dict:
    """Load a cadence log, quarantining the file if it is corrupt.

    A malformed cadence log must never kill a venture (audit A3 — a YAML
    error in 5-plur's log surfaced as a permanent sense_error and the
    venture never woke for a month). On parse failure the file is renamed
    to [name].broken-[UTC timestamp].bak next to the original, the error
    is logged loudly, and an empty log is returned so cadences fire fresh.
    """
    path = Path(path)
    # QUARANTINE ONLY A FILE. This renames `path`, so a caller that passes
    # anything other than the log renames that instead — and on 2026-08-31 a
    # caller passed the SPACE DIRECTORY, whose read raised IsADirectoryError
    # and sent eight spaces (1-datafund … 8-firm) to `<space>.broken-*.bak` in
    # one call. They were restored intact, but nothing here made that a near
    # miss rather than a loss.
    #
    # A missing file is not corruption either: it is the normal state of a
    # venture that has never run a cadence, and quarantining it would be
    # renaming something that does not exist.
    if path.exists() and not path.is_file():
        logger.error(
            "REFUSING to quarantine %s — not a file. A cadence log path was "
            "expected; this looks like a caller passing a directory.", path)
        return {}
    try:
        return load_cadence_log(path)
    except Exception as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine = path.with_name(f"{path.name}.broken-{stamp}.bak")
        try:
            path.rename(quarantine)
            logger.error(
                "CORRUPT cadence log %s (%s) — quarantined to %s, starting fresh",
                path, exc, quarantine,
            )
        except OSError as rename_exc:
            logger.error(
                "CORRUPT cadence log %s (%s) — quarantine rename failed (%s); "
                "ignoring file and starting fresh",
                path, exc, rename_exc,
            )
        return {}


# ---------------------------------------------------------------------------
# Org-file dedup helpers
# ---------------------------------------------------------------------------


_HEADING_STATE_RE = re.compile(r"^\*+\s+([A-Z]+)\b")
_CADENCE_PROP_RE = re.compile(r"^\s*:CADENCE:\s*(\S.*)")


def collect_active_cadences(org_content: str) -> set:
    """Collect :CADENCE: property values for tasks that are still open.

    Walks org content line by line, tracking each heading's TODO state.
    CADENCE properties under DONE/CANCELLED headings are excluded so a
    completed cadence task no longer suppresses re-fires forever
    (audit A4). Properties under headings with no recognised state are
    kept — conservative: suppression stays when the state is unknown.
    """
    active = set()
    current_state = None
    for line in org_content.splitlines():
        if line.startswith("*"):
            match = _HEADING_STATE_RE.match(line)
            current_state = match.group(1) if match else None
            continue
        match = _CADENCE_PROP_RE.match(line)
        if match and current_state not in COMPLETED_TASK_STATES:
            active.add(match.group(1).strip())
    return active


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


BLOCKED_COOLDOWN_DAYS = 7


def find_overdue_cadences(
    roles: dict,
    cadence_log: dict,
    today: Optional[date] = None,
) -> list:
    """Find all overdue cadences across all roles.

    A cadence is overdue if:
    - It has never run (not in cadence_log), OR
    - today - last_run >= frequency_window

    Blocked cadences (result: blocked) get a 7-day cooldown: they won't
    be flagged as overdue until 7 days after last_run, preventing
    re-checking something the agent already determined is blocked while
    still re-checking periodically in case the blocker is resolved.

    Returns a list of CadenceEntry sorted by:
    1. Frequency priority (daily first, then weekly, monthly, quarterly)
    2. Days overdue descending (most overdue first within same frequency)

    Args:
        roles: Role dict from venture.yaml (keyed by role id, each with "cadences" sub-dict).
        cadence_log: Nested dict: {role: {frequency: {cadence_name: "YYYY-MM-DD"}}}.
            Also checks flat-key format {role.cadence_name: {last_run, result}}
            written by the heartbeat system.
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

                # Also check flat-key format from heartbeat: "role.cadence-name"
                flat_key = f"{role_id}.{name}"
                flat_entry = cadence_log.get(flat_key, {})

                # Determine if blocked (from flat-key heartbeat log)
                is_blocked = (isinstance(flat_entry, dict)
                              and flat_entry.get("result") == "blocked")

                # Use flat-key last_run if nested doesn't have it
                if last_run_str is None and isinstance(flat_entry, dict):
                    last_run_str = flat_entry.get("last_run")

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

                    # Blocked cadences: apply cooldown before re-checking
                    effective_window = (BLOCKED_COOLDOWN_DAYS
                                        if is_blocked
                                        else window.days)

                    if delta >= effective_window:
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


def _stable_task_id(entry: CadenceEntry, venture_name: str) -> str:
    """Build a stable, line-independent ID for a cadence task.

    Format: ``cadence-{venture-slug}-{cadence-slug}-{YYYY-MM-DD}``.

    Stable IDs replace the parser's ``next_actions-L<line>`` fallback so the
    claim mechanism keys on identity, not file position. Two cadence runs on
    the same day for the same cadence/venture intentionally collide on ID —
    the writer treats that as idempotent (no duplicate task).
    """
    def slug(s: str) -> str:
        return ''.join(c if c.isalnum() else '-' for c in s.lower()).strip('-')
    today = date.today().isoformat()
    return f"cadence-{slug(venture_name)}-{slug(entry.cadence_name)}-{today}"


def generate_cadence_task(entry: CadenceEntry, venture_name: str) -> dict:
    """Generate a simple org task dict for an overdue cadence entry.

    Returns a dict with:
        heading   — task title
        state     — "TODO"
        tags_str  — org-mode tag string e.g. ":AI:venture:cmo:"
        properties — dict with ID, ROLE, VENTURE, CADENCE, FREQUENCY, DAYS_OVERDUE
    """
    heading = f"[{venture_name}] {entry.cadence_name} ({entry.frequency})"
    tags_str = f":AI:venture:{entry.role}:"

    return {
        "heading": heading,
        "state": "TODO",
        "tags_str": tags_str,
        "properties": {
            "ID": _stable_task_id(entry, venture_name),
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

    # Load cadence template if available
    template = load_cadence_template(entry.cadence_name)

    # Bootstrap: instructions for the nightshift agent to orient
    if template:
        bootstrap = (
            f"Read venture.yaml and role '{entry.role}' definition. "
            f"Check previous cadence results in .datacore/cadence-log.yaml. "
            f"Follow the cadence template below for '{entry.cadence_name}'."
        )
    else:
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

    if template:
        base["body"] = template

    return base
