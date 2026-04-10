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
    approval_threshold: float = 25.0
    ledger: str = ".datacore/state/venture/budget-ledger.yaml"


@dataclass
class RoleConfig:
    """A named role within a venture, with cadences."""

    description: str = ""
    cadences: dict = field(default_factory=dict)  # {daily: [...], weekly: [...], ...}
    budget_authority: float = 0


@dataclass
class RepoConfig:
    """A GitHub repository associated with a venture."""

    name: str = ""
    role: str = ""
    owner: str = ""
    scan: bool = False
    labels_ai: list = field(default_factory=list)


@dataclass
class GitHubConfig:
    """GitHub config for a venture."""

    org: str = ""
    repos: list = field(default_factory=list)  # List[RepoConfig]


@dataclass
class NightshiftConfig:
    """Nightshift execution settings for a venture."""

    enabled: bool = True
    max_parallel_agents: int = 2
    timeout_minutes: int = 60
    priority: str = "normal"


@dataclass
class VentureConfig:
    """Full configuration for an autonomous venture."""

    name: str = ""
    description: str = ""
    stage: str = "discovery"
    space: str = ""
    autonomy: int = 0
    budget: BudgetConfig = field(default_factory=lambda: BudgetConfig(ceiling=0))
    thesis: str = ""
    north_star: str = ""
    target_customer: str = ""
    roles: dict = field(default_factory=dict)  # {name: RoleConfig}
    hypotheses_file: str = "hypotheses.yaml"
    modules: dict = field(default_factory=dict)
    tracks: list = field(default_factory=list)
    github: Optional[GitHubConfig] = None
    nightshift: Optional[NightshiftConfig] = None
    tags: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_venture(data: dict) -> list[str]:
    """Validate a raw venture dict.

    Returns a list of error strings. An empty list means the data is valid.
    """
    errors: list[str] = []

    for required in ("name",):
        if not data.get(required):
            errors.append(f"Missing required field: '{required}'")

    stage = data.get("stage")
    if stage and stage not in VALID_STAGES:
        errors.append(
            f"Invalid stage '{stage}'. Must be one of: {', '.join(VALID_STAGES)}"
        )

    autonomy = data.get("autonomy")
    if autonomy is not None:
        if not isinstance(autonomy, int) or autonomy < 0 or autonomy > MAX_AUTONOMY:
            errors.append(
                f"Invalid autonomy '{autonomy}'. Must be an integer 0-{MAX_AUTONOMY}"
            )

    budget = data.get("budget")
    if budget is not None:
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
        approval_threshold=float(data.get("approval_threshold", 25.0)),
        ledger=data.get("ledger", ".datacore/state/venture/budget-ledger.yaml"),
    )


def _parse_roles(roles_data) -> dict:
    """Parse roles from venture.yaml. Accepts dict format:
    roles:
      operator:
        description: "..."
        cadences: {daily: [...], weekly: [...]}
        budget_authority: 10
    """
    result = {}
    if isinstance(roles_data, dict):
        for name, rdata in roles_data.items():
            if isinstance(rdata, dict):
                result[name] = RoleConfig(
                    description=rdata.get("description", ""),
                    cadences=rdata.get("cadences", {}),
                    budget_authority=float(rdata.get("budget_authority", 0)),
                )
    return result


def _parse_repo(data: dict) -> RepoConfig:
    return RepoConfig(
        name=data.get("name", ""),
        role=data.get("role", ""),
        owner=data.get("owner", ""),
        scan=bool(data.get("scan", False)),
        labels_ai=list(data.get("labels_ai", [])),
    )


def _parse_github(data: dict) -> GitHubConfig:
    return GitHubConfig(
        org=data.get("org", ""),
        repos=[_parse_repo(r) for r in data.get("repos", [])],
    )


def _parse_nightshift(data: dict) -> NightshiftConfig:
    return NightshiftConfig(
        enabled=bool(data.get("enabled", True)),
        max_parallel_agents=int(data.get("max_parallel_agents", 2)),
        timeout_minutes=int(data.get("timeout_minutes", 60)),
        priority=data.get("priority", "normal"),
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
        stage=data.get("stage", "discovery"),
        space=data.get("space", ""),
        autonomy=int(data.get("autonomy", 0)),
        budget=_parse_budget(data["budget"]) if data.get("budget") else BudgetConfig(ceiling=0),
        thesis=data.get("thesis", ""),
        north_star=data.get("north_star", ""),
        target_customer=data.get("target_customer", ""),
        roles=_parse_roles(data.get("roles", {})),
        hypotheses_file=data.get("hypotheses_file", "hypotheses.yaml"),
        modules=data.get("modules", {}),
        tracks=data.get("tracks", []),
        github=_parse_github(github_raw) if github_raw else None,
        nightshift=_parse_nightshift(nightshift_raw) if nightshift_raw else None,
        tags=list(data.get("tags", [])),
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
