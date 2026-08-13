"""Agents must record what they do in the outside world (DIP-0038/0046).

The ledger metered spend to the cent while an irreversible, publicly visible
action by an autonomous agent — posting to X — left no trace at all. A task can
be re-derived from org; a tweet cannot be un-sent.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.log import read_events  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "1-datafund" / ".datacore" / "events").mkdir(parents=True)
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATACORE_ACTOR", "data")
    import importlib, ledger_attest
    importlib.reload(ledger_attest)
    return tmp_path, ledger_attest


def test_an_external_action_lands_in_the_ledger(root):
    tmp, mod = root
    h = mod.attest("x.post", ref="123", detail="hello world")
    assert h
    evs = read_events(tmp / "1-datafund")
    assert len(evs) == 1
    e = evs[0]
    assert e.type == "artifact.attest" and e.actor == "data"
    assert e.payload["kind"] == "x.post" and e.payload["ref"] == "123"


def test_it_never_fails_the_caller(root):
    """A tweet that went out but could not be recorded is still a tweet that
    went out. Turning an accounting gap into a publishing outage is worse."""
    tmp, mod = root
    import importlib, os
    os.environ["DATACORE_ROOT"] = "/nonexistent/path/xyz"
    importlib.reload(mod)
    assert mod.attest("x.post", ref="9") is None      # degraded, not raised


def test_detail_is_truncated(root):
    tmp, mod = root
    mod.attest("x.post", ref="1", detail="x" * 900)
    e = read_events(tmp / "1-datafund")[0]
    assert len(e.payload["detail"]) <= 280


def test_actor_is_not_guessed_from_hostname(root, monkeypatch):
    """DIP-0044: winston's hostname is `bridge`, hermes runs `tris`."""
    tmp, mod = root
    monkeypatch.setenv("DATACORE_ACTOR", "winston")
    import importlib
    importlib.reload(mod)
    mod.attest("x.post", ref="2")
    assert read_events(tmp / "1-datafund")[-1].actor == "winston"
