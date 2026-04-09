"""
venture_loader.py — Load and validate venture.yaml configuration files.

This is the foundation of the Autonomous Venture Framework. All other
components (cadence engine, budget tracker, hypothesis tracker) depend
on VentureConfig.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


VALID_STAGES = ("discovery", "validation", "growth", "maturity")
MAX_AUTONOMY = 3


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BudgetConfig:
    """Budget limits for a venture."""

    ceiling: float
    ai_tokens: float = 0.0
    real_spend: float = 0.0


@dataclass
class RoleConfig:
    """A single agent role within a venture."""

    id: str
    agent: str
    cadence: str  # e.g. "daily", "weekly", "monthly"
    enabled: bool = True


@dataclass
class RepoConfig:
    """A GitHub repository associated with a venture."""

    name: str
    role: str = ""  # e.g. "core", "output", "infra"


@dataclass
class GitHubConfig:
    """GitHub organisation and repo list for a venture."""

    org: str
    repos: list[RepoConfig] = field(default_factory=list)


@dataclass
class NightshiftConfig:
    """Nightshift execution settings for a venture."""

    enabled: bool = True
    max_parallel_agents: int = 2
    timeout_minutes: int = 60


@dataclass
class VentureConfig:
    """Full configuration for an autonomous venture."""

    name: str
    description: str
    stage: str
    space: str
    autonomy: int
    budget: BudgetConfig
    thesis: str = ""
    roles: list[RoleConfig] = field(default_factory=list)
    github: Optional[GitHubConfig] = None
    nightshift: Optional[NightshiftConfig] = None
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_venture(data: dict) -> list[str]:
    """Validate a raw venture dict.

    Returns a list of error strings. An empty list means the data is valid.
    """
    errors: list[str] = []

    # Required string fields
    for required in ("name", "description", "stage", "space"):
        if not data.get(required):
            errors.append(f"Missing required field: '{required}'")

    # Stage must be one of VALID_STAGES
    stage = data.get("stage")
    if stage and stage not in VALID_STAGES:
        errors.append(
            f"Invalid stage '{stage}'. Must be one of: {', '.join(VALID_STAGES)}"
        )

    # Autonomy must be 0–MAX_AUTONOMY
    autonomy = data.get("autonomy")
    if autonomy is not None:
        if not isinstance(autonomy, int) or autonomy < 0 or autonomy > MAX_AUTONOMY:
            errors.append(
                f"Invalid autonomy '{autonomy}'. Must be an integer 0–{MAX_AUTONOMY}"
            )

    # Budget validation
    budget = data.get("budget")
    if budget is None:
        errors.append("Missing required field: 'budget'")
    else:
        ceiling = budget.get("ceiling", 0.0)
        ai_tokens = budget.get("ai_tokens", 0.0)
        real_spend = budget.get("real_spend", 0.0)
        if ai_tokens + real_spend > ceiling:
            errors.append(
                f"budget error: ai_tokens ({ai_tokens}) + real_spend ({real_spend}) "
                f"= {ai_tokens + real_spend} exceeds ceiling ({ceiling})"
            )

    return errors


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _parse_budget(data: dict) -> BudgetConfig:
    return BudgetConfig(
        ceiling=float(data.get("ceiling", 0.0)),
        ai_tokens=float(data.get("ai_tokens", 0.0)),
        real_spend=float(data.get("real_spend", 0.0)),
    )


def _parse_role(data: dict) -> RoleConfig:
    return RoleConfig(
        id=data["id"],
        agent=data["agent"],
        cadence=data.get("cadence", "daily"),
        enabled=bool(data.get("enabled", True)),
    )


def _parse_repo(data: dict) -> RepoConfig:
    return RepoConfig(
        name=data["name"],
        role=data.get("role", ""),
    )


def _parse_github(data: dict) -> GitHubConfig:
    return GitHubConfig(
        org=data["org"],
        repos=[_parse_repo(r) for r in data.get("repos", [])],
    )


def _parse_nightshift(data: dict) -> NightshiftConfig:
    return NightshiftConfig(
        enabled=bool(data.get("enabled", True)),
        max_parallel_agents=int(data.get("max_parallel_agents", 2)),
        timeout_minutes=int(data.get("timeout_minutes", 60)),
    )


def load_venture(data: dict) -> VentureConfig:
    """Parse a raw venture dict into a VentureConfig.

    Raises ValueError with all validation errors if the data is invalid.
    """
    errors = validate_venture(data)
    if errors:
        raise ValueError("Invalid venture configuration:\n" + "\n".join(f"  - {e}" for e in errors))

    github_raw = data.get("github")
    nightshift_raw = data.get("nightshift")

    return VentureConfig(
        name=data["name"],
        description=data.get("description", ""),
        stage=data["stage"],
        space=data["space"],
        autonomy=int(data.get("autonomy", 0)),
        budget=_parse_budget(data["budget"]),
        thesis=data.get("thesis", ""),
        roles=[_parse_role(r) for r in data.get("roles", [])],
        github=_parse_github(github_raw) if github_raw else None,
        nightshift=_parse_nightshift(nightshift_raw) if nightshift_raw else None,
        tags=list(data.get("tags", [])),
        meta=dict(data.get("meta", {})),
    )


def load_venture_file(path: Path) -> VentureConfig:
    """Load a VentureConfig from a YAML file.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the YAML is invalid or fails validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Venture file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Venture file must be a YAML mapping, got: {type(data).__name__}")

    return load_venture(data)
