"""Tests for hypothesis_tracker.py — TDD implementation."""
import pytest
import yaml
from pathlib import Path
from dataclasses import asdict

from ventures.lib.hypothesis_tracker import (
    Hypothesis,
    HypothesisBoard,
    load_hypotheses,
    load_hypotheses_file,
    add_hypothesis,
    move_hypothesis,
    summary,
)


# --- Fixtures ---

SAMPLE_DICT = {
    "venture": "forge",
    "board": {
        "backlog": [
            {
                "id": "H001",
                "statement": "Etsy buyers will pay $15 for AI-generated wall art",
                "proposed_by": "gregor",
                "proposed_date": "2026-04-01",
                "metric": "10 sales in 30 days",
                "estimated_budget": 50.0,
                "estimated_duration": "30d",
            }
        ],
        "active": [
            {
                "id": "H002",
                "statement": "SEO-optimized listings increase click-through by 20%",
                "proposed_by": "gregor",
                "proposed_date": "2026-03-15",
                "metric": "CTR > 5%",
                "budget": 100.0,
                "status": "running",
                "experiments": 2,
                "current_result": "CTR at 4.2%",
                "next_experiment": "A/B title test",
                "deadline": "2026-04-30",
            }
        ],
        "validated": [
            {
                "id": "H003",
                "statement": "Digital downloads have 80%+ margin",
                "proposed_by": "gregor",
                "proposed_date": "2026-02-01",
                "result": "confirmed, 85% margin",
                "validated_date": "2026-03-01",
                "learnings": "Focus on digital-only products",
                "action_taken": "Shifted all new listings to digital",
            }
        ],
        "invalidated": [
            {
                "id": "H004",
                "statement": "Phone cases sell well with AI art",
                "proposed_by": "gregor",
                "proposed_date": "2026-02-15",
                "result": "only 1 sale in 60 days",
                "invalidated_date": "2026-04-15",
                "learnings": "Physical products have low margins and high competition",
                "action_taken": "Abandoned phone case category",
            }
        ],
    },
}


# --- Tests ---

def test_load_hypotheses_from_dict():
    board = load_hypotheses(SAMPLE_DICT)
    assert isinstance(board, HypothesisBoard)
    assert board.venture == "forge"
    assert len(board.backlog) == 1
    assert len(board.active) == 1
    assert len(board.validated) == 1
    assert len(board.invalidated) == 1

    backlog_h = board.backlog[0]
    assert isinstance(backlog_h, Hypothesis)
    assert backlog_h.id == "H001"
    assert backlog_h.statement == "Etsy buyers will pay $15 for AI-generated wall art"
    assert backlog_h.estimated_budget == 50.0

    active_h = board.active[0]
    assert active_h.id == "H002"
    assert active_h.experiments == 2
    assert active_h.current_result == "CTR at 4.2%"


def test_load_hypotheses_from_file(tmp_path):
    yaml_file = tmp_path / "hypotheses.yaml"
    yaml_file.write_text(yaml.dump(SAMPLE_DICT))

    board = load_hypotheses_file(yaml_file)
    assert isinstance(board, HypothesisBoard)
    assert board.venture == "forge"
    assert len(board.backlog) == 1
    assert board.backlog[0].id == "H001"
    assert len(board.validated) == 1
    assert board.validated[0].validated_date == "2026-03-01"


def test_add_hypothesis_to_backlog():
    board = load_hypotheses({"venture": "test", "board": {"backlog": [], "active": [], "validated": [], "invalidated": []}})
    new_h = Hypothesis(
        id="H010",
        statement="New hypothesis",
        proposed_by="alice",
        proposed_date="2026-04-09",
        metric="5 signups",
        estimated_budget=25.0,
        estimated_duration="14d",
    )
    updated = add_hypothesis(board, new_h)
    assert len(updated.backlog) == 1
    assert updated.backlog[0].id == "H010"
    assert updated.backlog[0].statement == "New hypothesis"
    # Original board unchanged (functional)
    assert len(board.backlog) == 0


def test_move_hypothesis_backlog_to_active():
    board = load_hypotheses(SAMPLE_DICT)
    extra = {
        "status": "running",
        "budget": 75.0,
        "deadline": "2026-05-09",
        "next_experiment": "Launch first ad",
    }
    updated = move_hypothesis(board, "H001", "backlog", "active", extra=extra)

    assert len(updated.backlog) == 0
    assert len(updated.active) == 2

    moved = next(h for h in updated.active if h.id == "H001")
    assert moved.status == "running"
    assert moved.budget == 75.0
    assert moved.deadline == "2026-05-09"
    assert moved.next_experiment == "Launch first ad"
    # Original statement preserved
    assert moved.statement == "Etsy buyers will pay $15 for AI-generated wall art"


def test_move_hypothesis_active_to_validated():
    board = load_hypotheses(SAMPLE_DICT)
    extra = {
        "result": "Achieved 12 sales in 28 days",
        "validated_date": "2026-04-28",
        "learnings": "AI wall art resonates with home decor buyers",
        "action_taken": "Scale ad spend 3x",
    }
    # First move H001 to active so we have something to validate
    board = move_hypothesis(board, "H001", "backlog", "active")
    updated = move_hypothesis(board, "H002", "active", "validated", extra=extra)

    assert len(updated.active) == 1  # H001 remains
    assert len(updated.validated) == 2

    validated_h = next(h for h in updated.validated if h.id == "H002")
    assert validated_h.result == "Achieved 12 sales in 28 days"
    assert validated_h.validated_date == "2026-04-28"
    assert validated_h.learnings == "AI wall art resonates with home decor buyers"


def test_move_hypothesis_not_found():
    board = load_hypotheses(SAMPLE_DICT)
    with pytest.raises(ValueError, match="H999"):
        move_hypothesis(board, "H999", "backlog", "active")


def test_summary():
    board = load_hypotheses(SAMPLE_DICT)
    result = summary(board)
    assert result == {
        "backlog": 1,
        "active": 1,
        "validated": 1,
        "invalidated": 1,
        "total": 4,
    }

    # Empty board
    empty_board = HypothesisBoard(venture="empty")
    empty_summary = summary(empty_board)
    assert empty_summary == {
        "backlog": 0,
        "active": 0,
        "validated": 0,
        "invalidated": 0,
        "total": 0,
    }
