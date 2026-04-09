"""Hypothesis Tracker — Lean Startup Build-Measure-Learn board.

Hypotheses flow through four lanes: backlog → active → validated/invalidated.
Each lane transition can carry extra data (metrics, results, learnings).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Hypothesis:
    id: str = ""
    statement: str = ""
    proposed_by: str = ""
    proposed_date: str = ""
    estimated_budget: float = 0
    estimated_duration: str = ""
    metric: str = ""
    budget: float = 0
    status: str = ""
    experiments: int = 0
    current_result: str = ""
    next_experiment: str = ""
    deadline: str = ""
    result: str = ""
    validated_date: str = ""
    invalidated_date: str = ""
    learnings: str = ""
    action_taken: str = ""


@dataclass
class HypothesisBoard:
    venture: str = ""
    backlog: list = field(default_factory=list)    # List[Hypothesis]
    active: list = field(default_factory=list)
    validated: list = field(default_factory=list)
    invalidated: list = field(default_factory=list)


def _hypothesis_from_dict(data: dict) -> Hypothesis:
    """Create a Hypothesis from a dict, ignoring unknown keys."""
    known_fields = Hypothesis.__dataclass_fields__.keys()
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return Hypothesis(**filtered)


def load_hypotheses(data: dict) -> HypothesisBoard:
    """Parse a HypothesisBoard from a dict (e.g. loaded from YAML)."""
    board_data = data.get("board", {})
    return HypothesisBoard(
        venture=data.get("venture", ""),
        backlog=[_hypothesis_from_dict(h) for h in board_data.get("backlog", [])],
        active=[_hypothesis_from_dict(h) for h in board_data.get("active", [])],
        validated=[_hypothesis_from_dict(h) for h in board_data.get("validated", [])],
        invalidated=[_hypothesis_from_dict(h) for h in board_data.get("invalidated", [])],
    )


def load_hypotheses_file(path: Path) -> HypothesisBoard:
    """Load a HypothesisBoard from a YAML file."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return load_hypotheses(data)


def add_hypothesis(board: HypothesisBoard, hypothesis: Hypothesis) -> HypothesisBoard:
    """Add a hypothesis to the backlog. Returns a new board (immutable)."""
    new_board = copy.deepcopy(board)
    new_board.backlog.append(copy.deepcopy(hypothesis))
    return new_board


def move_hypothesis(
    board: HypothesisBoard,
    hypothesis_id: str,
    from_lane: str,
    to_lane: str,
    extra: dict[str, Any] | None = None,
) -> HypothesisBoard:
    """Move a hypothesis between lanes.

    Args:
        board: The current board state.
        hypothesis_id: The ID of the hypothesis to move.
        from_lane: Source lane name (backlog/active/validated/invalidated).
        to_lane: Destination lane name.
        extra: Optional dict of field updates to apply during the move.

    Returns:
        A new HypothesisBoard with the hypothesis moved and updated.

    Raises:
        ValueError: If the hypothesis is not found in from_lane.
    """
    new_board = copy.deepcopy(board)

    source: list = getattr(new_board, from_lane)
    destination: list = getattr(new_board, to_lane)

    # Find the hypothesis in the source lane
    idx = next((i for i, h in enumerate(source) if h.id == hypothesis_id), None)
    if idx is None:
        raise ValueError(
            f"Hypothesis '{hypothesis_id}' not found in lane '{from_lane}'"
        )

    hypothesis = source.pop(idx)

    # Apply extra field updates
    if extra:
        for key, value in extra.items():
            if hasattr(hypothesis, key):
                setattr(hypothesis, key, value)

    destination.append(hypothesis)
    return new_board


def summary(board: HypothesisBoard) -> dict:
    """Return lane counts and total for the board."""
    backlog = len(board.backlog)
    active = len(board.active)
    validated = len(board.validated)
    invalidated = len(board.invalidated)
    return {
        "backlog": backlog,
        "active": active,
        "validated": validated,
        "invalidated": invalidated,
        "total": backlog + active + validated + invalidated,
    }
