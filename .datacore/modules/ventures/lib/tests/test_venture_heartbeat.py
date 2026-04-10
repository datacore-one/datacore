"""
test_venture_heartbeat.py — Tests for venture_heartbeat.py sanitization and post-execution.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from venture_heartbeat import _sanitize_cadence_key


# ---------------------------------------------------------------------------
# _sanitize_cadence_key (Issue 1: cadence log key pollution)
# ---------------------------------------------------------------------------


def test_sanitize_clean_key():
    """A clean role.cadence key passes through."""
    assert _sanitize_cadence_key("ceo.strategy-review") == ["ceo.strategy-review"]


def test_sanitize_strips_parenthetical():
    """Parenthetical comments are stripped."""
    assert _sanitize_cadence_key("ceo.strategy-review (weekly cadence - decision execution)") == ["ceo.strategy-review"]


def test_sanitize_rejects_none():
    """Keys containing 'none' are rejected."""
    assert _sanitize_cadence_key("none") == []
    assert _sanitize_cadence_key("none (all cadences already done)") == []
    assert _sanitize_cadence_key("None") == []


def test_sanitize_rejects_spaces():
    """Keys with spaces (that aren't parenthetical) are rejected."""
    assert _sanitize_cadence_key("some random text") == []


def test_sanitize_splits_comma_separated():
    """Comma-separated keys are split and each validated."""
    result = _sanitize_cadence_key("ceo.strategy-review, cmo.content-plan")
    assert result == ["ceo.strategy-review", "cmo.content-plan"]


def test_sanitize_mixed_valid_invalid():
    """Only valid keys from a comma-separated list are returned."""
    result = _sanitize_cadence_key("ceo.strategy-review, none, invalid key with spaces")
    assert result == ["ceo.strategy-review"]


def test_sanitize_empty_string():
    """Empty string returns empty list."""
    assert _sanitize_cadence_key("") == []


def test_sanitize_strips_parenthetical_from_comma_list():
    """Parenthetical stripping works on each item in a comma list."""
    result = _sanitize_cadence_key("ceo.review (weekly), cmo.plan (monthly)")
    assert result == ["ceo.review", "cmo.plan"]
