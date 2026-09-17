"""A locked browser profile is not a dead credential."""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import nlm_auth_sync as sync


def _auth_fails(monkeypatch, browser, credential_ok):
    monkeypatch.setattr(sync, "_nlm_bin", lambda: "nlm")
    monkeypatch.setattr(sync, "_run", lambda cmd, timeout: (1, "net::ERR_ABORTED"))
    monkeypatch.setattr(sync, "browser_holding_profile", lambda: browser)
    monkeypatch.setattr(sync, "check_local", lambda: credential_ok)
    monkeypatch.setattr(sync, "fingerprint", lambda path: "abc123")


def test_a_running_browser_over_a_working_credential_defers(monkeypatch, capsys):
    """The hourly refresh reported FAILED most of the working day, because the
    laptop's browser owns the profile nlm auth reads -- about a credential that
    was valid the whole time."""
    _auth_fails(monkeypatch, "Google Chrome", True)
    assert sync.refresh_local() == "deferred"
    out = capsys.readouterr().out
    assert "DEFERRED" in out and "Google Chrome" in out and "still works" in out


def test_a_running_browser_over_a_DEAD_credential_still_fails(monkeypatch, capsys):
    _auth_fails(monkeypatch, "Google Chrome", False)
    assert sync.refresh_local() == "failed"
    out = capsys.readouterr().out
    assert "FAILED" in out and "owns that profile" in out


def test_no_browser_running_is_a_plain_failure(monkeypatch, capsys):
    _auth_fails(monkeypatch, None, False)
    assert sync.refresh_local() == "failed"
    assert "DEFERRED" not in capsys.readouterr().out
