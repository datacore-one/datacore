"""A Hermes run records what the provider charged, not a character-count guess.

2026-10-26: Winston's Hermes runs cost about 21.9 USD; the ledger's spend
records (`executor:hermes:est`) added up to 16 cents, so the spend guard saw
about 1/140 of the real cost and never tripped. The wrapper now reads the
OpenRouter key's usage before and after the run and reports the difference.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "executors"))
import hermes_oneshot as H  # noqa: E402


def test_usage_delta_is_after_minus_before():
    readings = iter([12.5000, 12.7341])
    assert H.usage_delta_usd(lambda: next(readings), lambda: None) == pytest.approx(0.2341)


def test_no_delta_when_either_reading_is_missing():
    assert H.usage_delta_usd(lambda: None, lambda: None) is None
    readings = iter([12.5, None])
    assert H.usage_delta_usd(lambda: next(readings), lambda: None) is None


def test_usage_reads_only_from_openrouter(monkeypatch):
    asked = []
    monkeypatch.setattr(H, "_get_json", lambda url, key: asked.append(url) or {"data": {"usage": 3.5}})
    assert H.key_usage_usd("https://openrouter.ai/api/v1", "k") == 3.5
    assert asked == ["https://openrouter.ai/api/v1/key"]
    assert H.key_usage_usd("https://api.example.com/v1", "k") is None
    assert H.key_usage_usd("https://openrouter.ai/api/v1", "") is None


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    from executors import hermes as adapter
    import actor_identity
    registry = tmp_path / "principals.yaml"
    registry.write_text("principals:\n  fixture: {writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    monkeypatch.setenv("DATACORE_NO_SPEND", "1")
    monkeypatch.setenv("DATACORE_ACTOR", "worker")
    monkeypatch.setattr(adapter.os.path, "isfile", lambda path: True)
    return adapter


def test_reported_cost_replaces_the_estimate(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "run_process", lambda command, **kw: subprocess.CompletedProcess(
        command, 0, "done", "hermes_oneshot: cost_usd=0.2341\n"))
    ex = adapter.HermesExecutor()
    result = ex.run("Private request")
    assert result.cost_cents == 24
    assert ex._cost_estimated is False


def test_without_a_report_the_estimate_stays_marked(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "run_process", lambda command, **kw: subprocess.CompletedProcess(
        command, 0, "done", ""))
    ex = adapter.HermesExecutor()
    ex.run("Private request")
    assert ex._cost_estimated is True


def test_a_failed_run_still_reports_its_cost(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "run_process", lambda command, **kw: subprocess.CompletedProcess(
        command, 1, "partial", "hermes_oneshot: cost_usd=1.5\nhermes_oneshot: incomplete provider result\n"))
    result = adapter.HermesExecutor().run("Private request")
    assert result.error and result.cost_cents == 150


def test_a_failed_run_with_no_text_keeps_its_cost(adapter, monkeypatch):
    """The 402 and timeout runs of 2026-10-26 printed nothing, yet were paid for."""
    monkeypatch.setattr(adapter, "run_process", lambda command, **kw: subprocess.CompletedProcess(
        command, 1, "", "hermes_oneshot: cost_usd=0.9\nhermes_oneshot: no response produced\n"))
    result = adapter.HermesExecutor().run("Private request")
    assert result.error and "no response produced" in result.error and result.cost_cents == 90
