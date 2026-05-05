"""
venture_loader.py — Load and validate venture.yaml configuration files.

This is the foundation of the Autonomous Venture Framework. All other
components (cadence engine, budget tracker, hypothesis tracker) depend
on VentureConfig.

Migrated to Pydantic v2 in 2026-05-05 (Phase A.0.1) to unlock:
  * model_json_schema() for the codegen pipeline (pydantic2ts → TS types)
  * built-in validation at construction
  * direct use as FastAPI request/response models

Backward compatibility:
  * load_venture() still raises ValueError on invalid input (existing
    callers catch ValueError, not pydantic.ValidationError).
  * validate_venture() still returns list[str] for cross-field rules.
  * Field access via dot notation is unchanged.

Extra-field policy:
  * Top-level VentureConfig: extra="allow" — real venture.yaml files carry
    user-defined fields (audit_trail, hmm_live_prerequisites, etc.) that
    we must preserve.
  * Sub-blocks (BudgetConfig, RoleConfig, GitHubConfig, NightshiftConfig,
    RepoConfig): extra="forbid" — typos in well-defined sub-blocks should
    surface as errors, not silently drop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ── Stage state machine (Phase A.0.2) ────────────────────────────────────────

VALID_STAGES: tuple[str, ...] = (
    "proposed",
    "discovery",
    "validation",
    "growth",
    "maturity",
    "archived",
)

# Allowed transitions out of each stage. "archived" can only go back to
# "discovery" (restore). Any stage can be archived.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"discovery", "archived"},
    "discovery": {"validation", "archived"},
    "validation": {"growth", "discovery", "archived"},
    "growth": {"maturity", "validation", "archived"},
    "maturity": {"growth", "archived"},
    "archived": {"discovery"},
}

MAX_AUTONOMY = 3


def validate_transition(from_stage: str, to_stage: str) -> Optional[str]:
    """Check if a stage transition is allowed.

    Returns None if the transition is valid, or an error string explaining why
    it is not. A no-op transition (from == to) is always allowed.
    """
    if from_stage == to_stage:
        return None
    if from_stage not in VALID_STAGES:
        return f"Unknown source stage: '{from_stage}'"
    if to_stage not in VALID_STAGES:
        return f"Unknown target stage: '{to_stage}'"
    allowed = ALLOWED_TRANSITIONS.get(from_stage, set())
    if to_stage not in allowed:
        return (
            f"Transition not allowed: '{from_stage}' → '{to_stage}'. "
            f"Allowed from '{from_stage}': {sorted(allowed) or '(none)'}"
        )
    return None


# ── Sub-models (extra="forbid" — typos are errors) ───────────────────────────


class BudgetConfig(BaseModel):
    """Budget limits for a venture."""

    model_config = ConfigDict(extra="forbid")

    ceiling: float = 0.0
    ai_tokens: float = 0.0
    real_spend: float = 0.0
    approval_threshold: float = 25.0
    ledger: str = ".datacore/state/venture/budget-ledger.yaml"


class RoleConfig(BaseModel):
    """A named role within a venture, with cadences.

    extra="allow" because real venture.yaml files extend roles with
    documentation fields (agent, decisions, receives, responsibilities,
    boundary, metrics) that aren't part of the cadence-engine contract
    but should round-trip cleanly.
    """

    model_config = ConfigDict(extra="allow")

    description: str = ""
    cadences: dict[str, list[str]] = Field(default_factory=dict)
    budget_authority: float = 0.0


class RepoConfig(BaseModel):
    """A GitHub repository associated with a venture."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    role: str = ""
    owner: str = ""
    scan: bool = False
    labels_ai: list[str] = Field(default_factory=list)


class GitHubConfig(BaseModel):
    """GitHub config for a venture."""

    model_config = ConfigDict(extra="forbid")

    org: str = ""
    repos: list[RepoConfig] = Field(default_factory=list)


class NightshiftConfig(BaseModel):
    """Nightshift execution settings for a venture."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_parallel_agents: int = 2
    timeout_minutes: int = 60
    priority: str = "normal"


# ── Top-level model (extra="allow" — preserve user-added fields) ─────────────


class VentureConfig(BaseModel):
    """Full configuration for an autonomous venture.

    Top-level extras are preserved (`extra="allow"`) — real venture.yaml files
    carry domain-specific fields (e.g. `audit_trail`, `hmm_live_prerequisites`)
    that the loader must round-trip without dropping.
    """

    model_config = ConfigDict(extra="allow")

    name: str = ""
    description: str = ""
    stage: str = "discovery"
    space: str = ""
    autonomy: int = 0
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    thesis: str = ""
    north_star: str = ""
    target_customer: str = ""
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    hypotheses_file: str = "hypotheses.yaml"
    modules: dict[str, Any] = Field(default_factory=dict)
    # tracks is heterogeneous in real yamls — sometimes plain strings ("ops"),
    # sometimes dicts like {"marketing": ["content", "social"]}. Accept Any.
    tracks: list[Any] = Field(default_factory=list)
    github: Optional[GitHubConfig] = None
    nightshift: Optional[NightshiftConfig] = None
    tags: list[str] = Field(default_factory=list)


# ── Validation (cross-field rules + back-compat shape) ───────────────────────


def validate_venture(data: dict) -> list[str]:
    """Validate a raw venture dict.

    Returns a list of error strings. An empty list means the data is valid.

    Pydantic field-level validation runs at construction; this function adds
    cross-field rules (budget sum constraint) and the historical "name is
    required" check (Pydantic would default to empty string, but the daemon
    contract is that name must be non-empty).
    """
    errors: list[str] = []

    if not data.get("name"):
        errors.append("Missing required field: 'name'")

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


# ── Loaders ──────────────────────────────────────────────────────────────────


def load_venture(data: dict) -> VentureConfig:
    """Parse a raw venture dict into a VentureConfig.

    Raises ValueError with all validation errors if the data is invalid.

    Two validation passes:
      1. Cross-field rules via validate_venture() — emits aggregated errors.
      2. Pydantic construction — emits field-type errors (translated to
         ValueError so existing callers catch consistently).
    """
    errors = validate_venture(data)
    if errors:
        raise ValueError(
            "Invalid venture configuration:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    try:
        return VentureConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid venture configuration: {exc}") from exc


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
        raise ValueError(
            f"Venture file must be a YAML mapping, got: {type(data).__name__}"
        )

    return load_venture(data)
