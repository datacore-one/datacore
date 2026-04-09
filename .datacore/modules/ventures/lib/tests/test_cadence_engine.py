"""
test_cadence_engine.py — Tests for cadence_engine.py (TDD).
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from cadence_engine import (
    CadenceEntry,
    find_overdue_cadences,
    generate_cadence_task,
    load_cadence_log,
    save_cadence_log,
)

# Fixed test date: 2026-04-09
TODAY = date(2026, 4, 9)

# Minimal roles dict matching venture.yaml format
ROLES = {
    "cmo": {
        "description": "Marketing",
        "cadences": {
            "daily": ["check-social", "check-email"],
            "weekly": ["content-calendar-review"],
        },
        "budget_authority": 10,
    }
}


# ---------------------------------------------------------------------------
# find_overdue_cadences
# ---------------------------------------------------------------------------


def test_find_overdue_daily_never_run():
    """Never-run cadences are overdue."""
    result = find_overdue_cadences(ROLES, cadence_log={}, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "check-social" in names
    assert "check-email" in names
    assert "content-calendar-review" in names


def test_find_overdue_daily_ran_today():
    """A daily cadence that ran today is NOT overdue."""
    log = {"cmo": {"daily": {"check-social": str(TODAY)}}}
    result = find_overdue_cadences(ROLES, cadence_log=log, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "check-social" not in names


def test_find_overdue_daily_ran_yesterday():
    """A daily cadence that ran yesterday IS overdue."""
    from datetime import timedelta
    yesterday = str(TODAY - timedelta(days=1))
    log = {"cmo": {"daily": {"check-social": yesterday}}}
    result = find_overdue_cadences(ROLES, cadence_log=log, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "check-social" in names


def test_find_overdue_weekly_within_window():
    """A weekly cadence that ran 3 days ago is NOT overdue."""
    from datetime import timedelta
    three_days_ago = str(TODAY - timedelta(days=3))
    log = {"cmo": {"weekly": {"content-calendar-review": three_days_ago}}}
    result = find_overdue_cadences(ROLES, cadence_log=log, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "content-calendar-review" not in names


def test_find_overdue_weekly_past_window():
    """A weekly cadence that ran 8 days ago IS overdue."""
    from datetime import timedelta
    eight_days_ago = str(TODAY - timedelta(days=8))
    log = {"cmo": {"weekly": {"content-calendar-review": eight_days_ago}}}
    result = find_overdue_cadences(ROLES, cadence_log=log, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "content-calendar-review" in names


def test_find_overdue_monthly_within_window():
    """A monthly cadence that ran 15 days ago is NOT overdue."""
    from datetime import timedelta
    roles = {
        "cmo": {
            "description": "Marketing",
            "cadences": {
                "monthly": ["strategy-review"],
            },
        }
    }
    fifteen_days_ago = str(TODAY - timedelta(days=15))
    log = {"cmo": {"monthly": {"strategy-review": fifteen_days_ago}}}
    result = find_overdue_cadences(roles, cadence_log=log, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "strategy-review" not in names


def test_find_overdue_monthly_past_window():
    """A monthly cadence that ran 35 days ago IS overdue."""
    from datetime import timedelta
    roles = {
        "cmo": {
            "description": "Marketing",
            "cadences": {
                "monthly": ["strategy-review"],
            },
        }
    }
    thirty_five_days_ago = str(TODAY - timedelta(days=35))
    log = {"cmo": {"monthly": {"strategy-review": thirty_five_days_ago}}}
    result = find_overdue_cadences(roles, cadence_log=log, today=TODAY)
    names = [e.cadence_name for e in result]
    assert "strategy-review" in names


def test_overdue_sorted_by_priority():
    """Overdue entries are sorted: daily first, then weekly, then monthly."""
    from datetime import timedelta
    roles = {
        "cmo": {
            "description": "Marketing",
            "cadences": {
                "daily": ["check-social"],
                "weekly": ["content-calendar-review"],
                "monthly": ["strategy-review"],
            },
        }
    }
    # All overdue: ran long ago
    long_ago = str(TODAY - timedelta(days=200))
    log = {
        "cmo": {
            "daily": {"check-social": long_ago},
            "weekly": {"content-calendar-review": long_ago},
            "monthly": {"strategy-review": long_ago},
        }
    }
    result = find_overdue_cadences(roles, cadence_log=log, today=TODAY)
    frequencies = [e.frequency for e in result]
    # Find indices
    daily_idx = next(i for i, f in enumerate(frequencies) if f == "daily")
    weekly_idx = next(i for i, f in enumerate(frequencies) if f == "weekly")
    monthly_idx = next(i for i, f in enumerate(frequencies) if f == "monthly")
    assert daily_idx < weekly_idx < monthly_idx


# ---------------------------------------------------------------------------
# load_save_cadence_log
# ---------------------------------------------------------------------------


def test_load_save_cadence_log():
    """Roundtrip: save then load returns identical data."""
    log = {
        "cmo": {
            "daily": {"check-social": "2026-04-08"},
            "weekly": {"content-calendar-review": "2026-04-01"},
        }
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "subdir" / "cadence-log.yaml"
        save_cadence_log(log, path)
        loaded = load_cadence_log(path)
    assert loaded == log


def test_load_cadence_log_missing_file():
    """Loading a missing file returns an empty dict."""
    path = Path("/tmp/nonexistent-cadence-log-xyz.yaml")
    result = load_cadence_log(path)
    assert result == {}


# ---------------------------------------------------------------------------
# generate_cadence_task
# ---------------------------------------------------------------------------


def test_generate_cadence_task():
    """Generated task has correct heading, state, tags, and properties."""
    entry = CadenceEntry(
        role="cmo",
        cadence_name="check-social",
        frequency="daily",
        days_overdue=1,
    )
    task = generate_cadence_task(entry, venture_name="megaphone")

    assert "heading" in task
    assert "check-social" in task["heading"]
    assert task["state"] == "TODO"
    assert ":AI:" in task["tags_str"]
    assert ":venture:" in task["tags_str"]
    assert "cmo" in task["tags_str"]

    props = task["properties"]
    assert props["ROLE"] == "cmo"
    assert props["VENTURE"] == "megaphone"
    assert props["CADENCE"] == "check-social"
    assert props["FREQUENCY"] == "daily"
    assert props["DAYS_OVERDUE"] == 1
