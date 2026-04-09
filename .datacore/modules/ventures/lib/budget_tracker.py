"""Budget tracker for venture spending limits with monthly reset."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from datetime import date
from typing import Optional

import yaml


@dataclass
class BudgetLedger:
    total_ai: float = 0
    total_real: float = 0
    entries: list = field(default_factory=list)  # List of dicts: {date, amount, category, description}
    month: str = ""  # "YYYY-MM"


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def load_ledger(path: Path, current_month: str = None) -> BudgetLedger:
    """Load ledger from YAML. Returns empty ledger if file missing.
    Resets totals/entries if the month has changed (monthly reset).
    """
    if current_month is None:
        current_month = _current_month()

    if not path.exists():
        return BudgetLedger(month=current_month)

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    stored_month = data.get("month", "")

    # Monthly reset: if stored month differs from current month, return a fresh ledger
    if stored_month != current_month:
        return BudgetLedger(month=current_month)

    return BudgetLedger(
        total_ai=float(data.get("total_ai", 0)),
        total_real=float(data.get("total_real", 0)),
        entries=data.get("entries", []) or [],
        month=stored_month,
    )


def save_ledger(ledger: BudgetLedger, path: Path) -> None:
    """Save ledger to YAML, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "month": ledger.month,
        "total_ai": ledger.total_ai,
        "total_real": ledger.total_real,
        "entries": ledger.entries,
    }
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def record_spend(
    ledger: BudgetLedger,
    amount: float,
    category: str,
    description: str,
    date_str: str,
) -> BudgetLedger:
    """Add a spend entry and update totals. Category is 'ai_tokens' or 'real_spend'."""
    entry = {
        "date": date_str,
        "amount": amount,
        "category": category,
        "description": description,
    }
    ledger.entries.append(entry)

    if category == "ai_tokens":
        ledger.total_ai += amount
    elif category == "real_spend":
        ledger.total_real += amount

    return ledger


def get_remaining(
    ledger: BudgetLedger,
    monthly_ceiling: float,
    ai_ceiling: float,
    real_ceiling: float,
) -> dict:
    """Return remaining budget headroom for total, ai, and real categories."""
    total_spent = ledger.total_ai + ledger.total_real
    return {
        "total": monthly_ceiling - total_spent,
        "ai": ai_ceiling - ledger.total_ai,
        "real": real_ceiling - ledger.total_real,
    }


def check_can_spend(
    ledger: BudgetLedger,
    amount: float,
    category: str,
    monthly_ceiling: float,
    ai_ceiling: float,
    real_ceiling: float,
    approval_threshold: float,
) -> tuple[bool, str]:
    """Check whether a spend is allowed.

    Returns (ok, reason). Fails if:
    - real_spend amount > approval_threshold (needs human approval)
    - would exceed category ceiling
    - would exceed monthly ceiling
    """
    # Real spend above approval threshold requires human approval
    if category == "real_spend" and amount > approval_threshold:
        return (
            False,
            f"Amount {amount} exceeds approval threshold {approval_threshold} — human approval required",
        )

    # Check category ceiling
    if category == "ai_tokens":
        if ledger.total_ai + amount > ai_ceiling:
            return (
                False,
                f"Would exceed AI tokens ceiling ({ai_ceiling}): current {ledger.total_ai} + {amount}",
            )
    elif category == "real_spend":
        if ledger.total_real + amount > real_ceiling:
            return (
                False,
                f"Would exceed real spend ceiling ({real_ceiling}): current {ledger.total_real} + {amount}",
            )

    # Check monthly ceiling
    total_spent = ledger.total_ai + ledger.total_real
    if total_spent + amount > monthly_ceiling:
        return (
            False,
            f"Would exceed monthly ceiling ({monthly_ceiling}): current {total_spent} + {amount}",
        )

    return (True, "")
