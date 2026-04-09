"""Tests for budget_tracker.py"""
import pytest
from pathlib import Path
from unittest.mock import patch
import tempfile
import yaml

from ventures.lib.budget_tracker import (
    BudgetLedger,
    load_ledger,
    save_ledger,
    record_spend,
    get_remaining,
    check_can_spend,
)


def test_load_empty_ledger(tmp_path):
    """Missing file returns empty ledger."""
    path = tmp_path / "nonexistent_ledger.yaml"
    ledger = load_ledger(path, current_month="2026-04")
    assert ledger.total_ai == 0
    assert ledger.total_real == 0
    assert ledger.entries == []
    assert ledger.month == "2026-04"


def test_record_ai_spend():
    """ai_tokens category updates total_ai."""
    ledger = BudgetLedger(month="2026-04")
    ledger = record_spend(ledger, 5.0, "ai_tokens", "GPT call", "2026-04-01")
    assert ledger.total_ai == 5.0
    assert ledger.total_real == 0.0
    assert len(ledger.entries) == 1
    assert ledger.entries[0]["amount"] == 5.0
    assert ledger.entries[0]["category"] == "ai_tokens"


def test_record_real_spend():
    """real_spend category updates total_real."""
    ledger = BudgetLedger(month="2026-04")
    ledger = record_spend(ledger, 25.0, "real_spend", "Domain purchase", "2026-04-02")
    assert ledger.total_real == 25.0
    assert ledger.total_ai == 0.0
    assert len(ledger.entries) == 1
    assert ledger.entries[0]["category"] == "real_spend"
    assert ledger.entries[0]["description"] == "Domain purchase"


def test_get_remaining():
    """Correct remaining calculations."""
    ledger = BudgetLedger(total_ai=30.0, total_real=50.0, month="2026-04")
    remaining = get_remaining(ledger, monthly_ceiling=200.0, ai_ceiling=100.0, real_ceiling=150.0)
    assert remaining["ai"] == 70.0
    assert remaining["real"] == 100.0
    assert remaining["total"] == 120.0


def test_check_can_spend_within_budget():
    """Returns (True, '') when all checks pass."""
    ledger = BudgetLedger(total_ai=10.0, total_real=20.0, month="2026-04")
    ok, reason = check_can_spend(
        ledger,
        amount=5.0,
        category="ai_tokens",
        monthly_ceiling=200.0,
        ai_ceiling=100.0,
        real_ceiling=150.0,
        approval_threshold=50.0,
    )
    assert ok is True
    assert reason == ""


def test_check_can_spend_exceeds_ceiling():
    """Returns (False, reason with 'ceiling') when category ceiling would be exceeded."""
    ledger = BudgetLedger(total_ai=95.0, total_real=0.0, month="2026-04")
    ok, reason = check_can_spend(
        ledger,
        amount=10.0,
        category="ai_tokens",
        monthly_ceiling=200.0,
        ai_ceiling=100.0,
        real_ceiling=150.0,
        approval_threshold=50.0,
    )
    assert ok is False
    assert "ceiling" in reason.lower()


def test_check_can_spend_needs_approval():
    """Real spend above threshold returns (False, reason with 'approval')."""
    ledger = BudgetLedger(total_ai=0.0, total_real=0.0, month="2026-04")
    ok, reason = check_can_spend(
        ledger,
        amount=75.0,
        category="real_spend",
        monthly_ceiling=500.0,
        ai_ceiling=100.0,
        real_ceiling=300.0,
        approval_threshold=50.0,
    )
    assert ok is False
    assert "approval" in reason.lower()


def test_save_and_load_roundtrip(tmp_path):
    """Save then load preserves data."""
    path = tmp_path / "ledger.yaml"
    ledger = BudgetLedger(month="2026-04")
    ledger = record_spend(ledger, 12.5, "ai_tokens", "OpenAI call", "2026-04-05")
    ledger = record_spend(ledger, 30.0, "real_spend", "Hosting fee", "2026-04-06")

    save_ledger(ledger, path)

    loaded = load_ledger(path, current_month="2026-04")
    assert loaded.total_ai == 12.5
    assert loaded.total_real == 30.0
    assert loaded.month == "2026-04"
    assert len(loaded.entries) == 2
    assert loaded.entries[0]["description"] == "OpenAI call"
    assert loaded.entries[1]["description"] == "Hosting fee"


def test_ledger_monthly_reset(tmp_path):
    """Totals reset when month changes."""
    path = tmp_path / "ledger.yaml"
    ledger = BudgetLedger(total_ai=50.0, total_real=80.0, month="2026-03")
    ledger = record_spend(ledger, 50.0, "ai_tokens", "March AI", "2026-03-15")
    save_ledger(ledger, path)

    # Load with a new month
    loaded = load_ledger(path, current_month="2026-04")
    assert loaded.total_ai == 0.0
    assert loaded.total_real == 0.0
    assert loaded.entries == []
    assert loaded.month == "2026-04"
