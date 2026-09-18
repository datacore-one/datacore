"""A break nobody can act on is said once, and its recovery is said too."""
import importlib.util
import pathlib
import sys

import pytest
from unittest.mock import MagicMock

LIB = pathlib.Path(__file__).resolve().parents[1]
for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    sys.modules.setdefault(name, MagicMock())
spec = importlib.util.spec_from_file_location("ro_state", LIB / "research_orchestrator.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def test_the_break_is_reported_once_then_stays_quiet(tmp_path):
    """Three weeks of identical daily alerts for an upstream break is how an
    alert channel becomes background noise (2026-09-18)."""
    state = tmp_path / "audio.json"
    first = R.record_audio_availability(False, "client RPC is stale", today="2026-09-18", path=state)
    assert first and R.AUDIO_BLOCKED_MARK in first and "2026-09-18" in first

    for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
        assert R.record_audio_availability(False, "client RPC is stale", today=day, path=state) is None


def test_silence_ends_by_itself_when_it_works_again(tmp_path):
    """Going quiet is only acceptable because the step still runs every night."""
    state = tmp_path / "audio.json"
    R.record_audio_availability(False, "client RPC is stale", today="2026-09-18", path=state)
    R.record_audio_availability(False, "client RPC is stale", today="2026-09-19", path=state)

    back = R.record_audio_availability(True, None, today="2026-09-25", path=state)

    assert back and R.AUDIO_RECOVERED_MARK in back and "2026-09-18" in back
    assert R.record_audio_availability(True, None, today="2026-09-26", path=state) is None


def test_a_break_after_a_recovery_is_reported_again(tmp_path):
    state = tmp_path / "audio.json"
    R.record_audio_availability(False, "x", today="2026-09-18", path=state)
    R.record_audio_availability(True, None, today="2026-09-25", path=state)
    again = R.record_audio_availability(False, "x", today="2026-10-02", path=state)
    assert again and R.AUDIO_BLOCKED_MARK in again and "2026-10-02" in again


def test_an_unreadable_state_file_still_reports_rather_than_crashing(tmp_path):
    state = tmp_path / "audio.json"
    state.write_text("{ not json")
    assert R.record_audio_availability(False, "x", today="2026-09-18", path=state)
