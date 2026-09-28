"""The Google Calendar helper is imported by long-running processes (the app's
background service, datacored). A library function that calls sys.exit()
there ends the event loop the service runs on: on 2026-09-28 a failed token
refresh inside a /cos request did exactly that, the service revoked its own
discovery files and kept serving as an orphan nobody could find.

So: library functions raise CredentialsUnavailable, never SystemExit, and
never open a browser consent flow unless the caller asked for it. Only the
command-line entry point turns the error into exit code 1.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from sync.adapters import gcal_auth  # noqa: E402


class _FakeCreds:
    def __init__(self, *, valid=False, expired=True, refresh_token="r", refresh_exc=None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._refresh_exc = refresh_exc

    def refresh(self, request):
        if self._refresh_exc is not None:
            raise self._refresh_exc
        self.valid = True

    def to_json(self):
        return json.dumps({"token": "t"})


@pytest.fixture
def creds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gcal_auth, "CREDS_DIR", tmp_path)
    monkeypatch.setattr(gcal_auth, "CLIENT_SECRETS_FILE", tmp_path / "client_secret.json")
    monkeypatch.setattr(gcal_auth, "_DEFAULT_TOKEN", tmp_path / "google_calendar_token.json")
    monkeypatch.setattr(gcal_auth, "_LEGACY_PICKLE_FILE", tmp_path / "legacy.pickle")
    return tmp_path


def _with_token(creds_dir, monkeypatch, creds):
    (creds_dir / "google_calendar_token.json").write_text(json.dumps({"token": "t"}))
    from google.oauth2 import credentials as gcreds
    monkeypatch.setattr(gcreds.Credentials, "from_authorized_user_info",
                        classmethod(lambda cls, info, scopes=None: creds))


def _no_browser(monkeypatch):
    from google_auth_oauthlib import flow

    def _refuse(*a, **k):
        raise AssertionError("a library call must never start the browser consent flow")
    monkeypatch.setattr(flow.InstalledAppFlow, "from_client_secrets_file", classmethod(_refuse))


def test_revoked_refresh_raises_credentials_unavailable_not_system_exit(creds_dir, monkeypatch):
    from google.auth.exceptions import RefreshError
    _with_token(creds_dir, monkeypatch, _FakeCreds(refresh_exc=RefreshError("invalid_grant")))
    with pytest.raises(gcal_auth.CredentialsUnavailable) as info:
        gcal_auth.get_credentials()
    assert info.value.needs_reauth is True
    assert "gcal_auth.py setup" in str(info.value)
    assert gcal_auth.REAUTH_COMMAND in str(info.value)


def test_network_failure_during_refresh_is_not_called_a_reauth(creds_dir, monkeypatch):
    # The 2026-09-28 failure was an SSL EOF talking to oauth2.googleapis.com:
    # the token was fine, and deleting it (what the old message advised) would
    # have destroyed a working credential.
    from google.auth.exceptions import TransportError
    _with_token(creds_dir, monkeypatch, _FakeCreds(refresh_exc=TransportError("SSLEOFError")))
    with pytest.raises(gcal_auth.CredentialsUnavailable) as info:
        gcal_auth.get_credentials()
    assert info.value.needs_reauth is False
    assert "Delete" not in str(info.value)


def test_missing_client_secret_raises(creds_dir, monkeypatch):
    _no_browser(monkeypatch)
    with pytest.raises(gcal_auth.CredentialsUnavailable) as info:
        gcal_auth.get_credentials()
    assert info.value.needs_reauth is True
    assert "client" in str(info.value).lower()


def test_library_call_never_opens_the_consent_flow(creds_dir, monkeypatch):
    (creds_dir / "client_secret.json").write_text("{}")
    _no_browser(monkeypatch)
    _with_token(creds_dir, monkeypatch, _FakeCreds(refresh_token=None))
    with pytest.raises(gcal_auth.CredentialsUnavailable) as info:
        gcal_auth.get_credentials()
    assert info.value.needs_reauth is True
    assert gcal_auth.REAUTH_COMMAND in str(info.value)


def test_list_events_propagates_the_exception_not_an_exit(creds_dir, monkeypatch):
    from google.auth.exceptions import RefreshError
    _with_token(creds_dir, monkeypatch, _FakeCreds(refresh_exc=RefreshError("invalid_grant")))
    with pytest.raises(gcal_auth.CredentialsUnavailable):
        gcal_auth.list_events()


@pytest.mark.parametrize("command", ["test", "list", "calendars"])
def test_command_line_still_exits_1(creds_dir, monkeypatch, capsys, command):
    from google.auth.exceptions import RefreshError
    _with_token(creds_dir, monkeypatch, _FakeCreds(refresh_exc=RefreshError("invalid_grant")))
    with pytest.raises(SystemExit) as info:
        gcal_auth.main([command])
    assert info.value.code == 1
    assert gcal_auth.REAUTH_COMMAND in capsys.readouterr().err


def test_setup_without_client_secret_still_exits_1(creds_dir, monkeypatch, capsys):
    _no_browser(monkeypatch)
    with pytest.raises(SystemExit) as info:
        gcal_auth.main(["setup"])
    assert info.value.code == 1
    assert "client" in capsys.readouterr().err.lower()


def test_setup_replaces_a_revoked_token_through_the_consent_flow(creds_dir, monkeypatch):
    # `setup` is the re-authentication command the errors point at; with a
    # revoked refresh token it must run the consent flow, not fail again.
    from google.auth.exceptions import RefreshError
    from google_auth_oauthlib import flow
    (creds_dir / "client_secret.json").write_text("{}")
    _with_token(creds_dir, monkeypatch, _FakeCreds(refresh_exc=RefreshError("invalid_grant")))

    class _Flow:
        def run_local_server(self, port=0):
            return _FakeCreds(valid=True, expired=False)
    monkeypatch.setattr(flow.InstalledAppFlow, "from_client_secrets_file",
                        classmethod(lambda cls, *a, **k: _Flow()))
    gcal_auth.main(["setup"])
    assert (creds_dir / "google_calendar_token.json").exists()
