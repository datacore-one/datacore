"""
Model Routing Helper for The Firm

Usage:
    from model_routing import pick_model
    provider, model_id, rationale = pick_model("reasoning", venture="forge")
    # Returns: ("anthropic", "claude-sonnet-4-6", "forge default->public-bulk, no escalation")

Privacy classes (restrictive to permissive): sensitive > internal > public-bulk
Sensitive tasks NEVER escalate to frontier models. This is a hard constraint.
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

_CONFIG: dict = {}


def _load_config() -> dict:
    """Load routing config, cached after first call."""
    global _CONFIG
    if _CONFIG:
        return _CONFIG
    config_path = Path(__file__).parent / "routing.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Routing config not found: {config_path}")
    with open(config_path) as f:
        _CONFIG = yaml.safe_load(f)
    return _CONFIG


def _parse_model(model_str: str) -> tuple[str, str]:
    """Split 'provider/model-id' into (provider, model_id)."""
    parts = model_str.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid model format: {model_str}")
    return parts[0], parts[1]


def _privacy_rank(config: dict, privacy_class: str) -> int:
    """Rank of a TASK/RESULT class (higher = more restrictive).

    A class outside `privacy_levels` (reasoning, high-stakes, an unknown task
    kind) ranks 0, the least private. On this side that is the fail-closed
    reading: it can only make a constraint bump the class, never waive one.
    """
    levels = config.get("privacy_levels", [])
    if privacy_class not in levels:
        return 0
    # List is ordered least to most restrictive, so index = rank
    return levels.index(privacy_class)


def _constraint_rank(config: dict, constraint: str) -> int:
    """Rank of a CONSTRAINT (a venture floor or a caller's privacy_class).

    Unlike `_privacy_rank`, an unknown constraint is an error. Ranking it 0
    made a typo (`confidential`) a constraint that permits everything --
    failing open on the one input whose job is to restrict.
    """
    levels = config.get("privacy_levels", [])
    if constraint not in levels:
        raise ValueError(f"Unknown privacy class: {constraint!r} "
                         f"(expected one of {levels})")
    return levels.index(constraint)


def _class_is_allowed(config: dict, task_class: str, privacy_constraint: str) -> bool:
    """Check if task_class respects privacy_constraint (constraint must be >= task_class)."""
    task_rank = _privacy_rank(config, task_class)
    constraint_rank = _constraint_rank(config, privacy_constraint)
    return task_rank >= constraint_rank


def pick_model(
    task_class: str,
    venture: Optional[str] = None,
    privacy_class: Optional[str] = None,
    budget_state: Optional[str] = None,  # Reserved for future budget tracking
) -> tuple[str, str, str]:
    """
    Select appropriate model based on task class, venture policy, and privacy constraints.

    Args:
        task_class: One of: sensitive, internal, public-bulk, reasoning, high-stakes
        venture: Optional venture name for policy lookup
        privacy_class: Hard constraint — never pick a model less private than this
        budget_state: Reserved for future budget-aware selection

    Returns:
        (provider, model_id, rationale) — e.g. ("anthropic", "claude-opus-4-7", "high-stakes direct")

    Guarantee: the class picked ranks at least as private as every constraint
    that applies -- the venture's privacy_floor (unless the task is one of
    that venture's `floor_exceptions`), the caller's privacy_class, and the
    task's own class when it is a privacy level. The only exceptions shipped
    are dmcc's client-report and contract-review (owner decision C3,
    2026-09-23). Constraints are
    applied AFTER venture escalation. They were applied before it until
    2026-09-23, so an escalation overwrote the floor: dmcc (floor internal,
    "never use public models") sent contract-review to claude-opus.

    Raises:
        ValueError: If a class or privacy constraint is unknown, or no valid model found
    """
    config = _load_config()
    classes = config.get("classes", {})
    ventures = config.get("ventures", {})

    # Resolve effective class via venture policy
    effective_class = task_class
    rationale_parts = []
    constraints: list[tuple[str, str]] = []   # (privacy class, label)

    if venture and venture in ventures:
        v_policy = ventures[venture]
        escalations = v_policy.get("escalations", {})
        default = v_policy.get("default_class", task_class)

        # Check if task_class is an escalation key
        if task_class in escalations:
            effective_class = escalations[task_class]
            rationale_parts.append(f"{venture} escalation {task_class} -> {effective_class}")
        elif task_class not in classes:
            effective_class = default
            rationale_parts.append(f"{venture} default -> {default}")

        v_floor = v_policy.get("privacy_floor")
        # Decision C3 (2026-09-23): a venture may list task types exempt from
        # ITS floor (routing.yaml `floor_exceptions`, the owner's explicit
        # choice). Only the venture floor is waived: the task's own privacy
        # level and the caller's privacy_class still apply below.
        exempt = task_class in (v_policy.get("floor_exceptions") or [])
        if v_floor and exempt:
            rationale_parts.append(
                f"{venture} floor exception for {task_class} (owner decision C3)")
        elif v_floor:
            constraints.append((v_floor, f"{venture} privacy floor"))
    elif venture:
        rationale_parts.append(f"venture '{venture}' not found, using class directly")

    # "Sensitive tasks NEVER escalate": a task that IS a privacy level is a
    # floor on its own result, whatever an escalation map says.
    if task_class in config.get("privacy_levels", []):
        constraints.append((task_class, f"task class {task_class}"))
    if privacy_class:
        constraints.append((privacy_class, "privacy constraint"))

    # Each bump strictly raises the rank, so after the loop the result ranks
    # >= every constraint (DatacoreSpec.Credentials.pick_respects_all).
    for floor, label in constraints:
        if _privacy_rank(config, effective_class) < _constraint_rank(config, floor):
            old_class = effective_class
            effective_class = floor
            if label == "privacy constraint":
                rationale_parts.append(f"privacy constraint {floor} overrides {old_class}")
            else:
                rationale_parts.append(f"{label} -> {floor} (overrides {old_class})")

    if not rationale_parts:
        rationale_parts.append(f"{effective_class} direct")

    # Validate class exists
    if effective_class not in classes:
        raise ValueError(f"Unknown task class: {effective_class}")

    # Get first available model from class
    models = classes[effective_class].get("models", [])
    if not models:
        raise ValueError(f"No models defined for class: {effective_class}")

    model_str = models[0]
    provider, model_id = _parse_model(model_str)

    rationale = "; ".join(rationale_parts)
    logger.info(f"[routing] {provider}/{model_id} — {rationale}")

    return provider, model_id, rationale


def list_classes() -> list[str]:
    """Return available task classes."""
    config = _load_config()
    return list(config.get("classes", {}).keys())


def list_ventures() -> list[str]:
    """Return configured ventures."""
    config = _load_config()
    return list(config.get("ventures", {}).keys())


if __name__ == "__main__":
    # Quick demo
    print("Classes:", list_classes())
    print("Ventures:", list_ventures())
    print()
    print("Examples:")
    print("  reasoning:", pick_model("reasoning"))
    print("  high-stakes + forge:", pick_model("high-stakes", venture="forge"))
    print("  reasoning + dmcc:", pick_model("reasoning", venture="dmcc"))
    print("  public-bulk + sensitive constraint:", pick_model("public-bulk", privacy_class="sensitive"))
