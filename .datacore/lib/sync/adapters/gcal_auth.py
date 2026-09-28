"""
Google Calendar Authentication Helper.

Supports multiple Google accounts via named tokens.
Each account gets its own token file: google_calendar_token_{account}.json

Usage:
    python gcal_auth.py setup                      # Setup default account
    python gcal_auth.py setup --account datafund    # Setup named account
    python gcal_auth.py test                        # Test default
    python gcal_auth.py test --account datafund     # Test named account
    python gcal_auth.py list                        # List events from default
    python gcal_auth.py list --account datafund     # List events from named account
    python gcal_auth.py calendars --account datafund
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from credential_files import calendar_token_path, write_private_text
from file_utils import file_lock

# Credentials storage
CREDS_DIR = Path(__file__).parent.parent.parent.parent / "env" / "credentials"
CLIENT_SECRETS_FILE = CREDS_DIR / "google_calendar_client_secret.json"

SCOPES = ['https://www.googleapis.com/auth/calendar']  # Full read/write access

# Default token (backwards compatible)
_DEFAULT_TOKEN = CREDS_DIR / "google_calendar_token.json"
_LEGACY_PICKLE_FILE = CREDS_DIR / "google_calendar_token.pickle"


def _token_file_for(account=None):
    return calendar_token_path(CREDS_DIR, _DEFAULT_TOKEN, account)


def _migrate_pickle_token():
    """Preserve legacy files, but never execute a serialized Python object."""
    if _LEGACY_PICKLE_FILE.exists() and not _DEFAULT_TOKEN.exists():
        print("Legacy pickle credentials are not loaded. Re-authenticate to create a JSON token; the original file is preserved.", file=sys.stderr)


REAUTH_COMMAND = "python3 ~/Data/.datacore/lib/sync/adapters/gcal_auth.py setup"


class CredentialsUnavailable(Exception):
    """No usable Google Calendar credentials.

    Library functions raise this; they never call sys.exit(). This module is
    imported by long-running processes (the app's background service), where
    a SystemExit raised in a request ends the service's event loop -- on
    2026-09-28 that orphaned the service. Only main() turns it into exit 1.

    needs_reauth is True when the owner has to run REAUTH_COMMAND (an
    interactive consent flow); False when the failure is transient (network)
    and retrying later is the fix.
    """

    def __init__(self, message, *, needs_reauth=True):
        super().__init__(message)
        self.needs_reauth = needs_reauth
        self.reauth_command = REAUTH_COMMAND if needs_reauth else None


def _reauth_command_for(account):
    if account and account != "default":
        return f"{REAUTH_COMMAND} --account {account}"
    return REAUTH_COMMAND


def get_credentials(account=None, interactive=False):
    """Return valid credentials, refreshing them when expired.

    Raises CredentialsUnavailable when there are none. The browser consent
    flow runs only when interactive=True (the `setup` command); a library
    caller never blocks on a browser.
    """
    with file_lock(_token_file_for(account), timeout=30):
        return _credentials_unlocked(account, interactive=interactive)


def _credentials_unlocked(account=None, interactive=False):
    """Get valid user credentials from storage (or, interactively, the consent flow)."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError, TransportError

    token_file = _token_file_for(account)
    reauth = _reauth_command_for(account)

    # Migrate legacy pickle for default account only
    if not account or account == "default":
        _migrate_pickle_token()

    creds = None

    if token_file.exists():
        try:
            token_data = json.loads(token_file.read_text())
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            print(f"WARNING: Failed to load token JSON: {e}", file=sys.stderr)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except TransportError as e:
            # Network trouble talking to Google (2026-09-28: an SSL EOF). The
            # token itself may be fine -- never advise deleting it for this.
            raise CredentialsUnavailable(
                f"Could not reach Google to refresh the calendar token ({e}); "
                "this is usually transient -- retry later. Re-authentication is "
                "not needed unless this persists.",
                needs_reauth=False) from e
        except RefreshError as e:
            if not interactive:
                raise CredentialsUnavailable(
                    f"Google Calendar token refresh was refused ({e}). "
                    f"Re-authenticate with: {reauth}") from e
            creds = None  # setup: replace the revoked token via the consent flow
        else:
            CREDS_DIR.mkdir(parents=True, exist_ok=True)
            write_private_text(token_file, creds.to_json())
            print(f"Credentials saved to {token_file}", file=sys.stderr)
            return creds

    if not CLIENT_SECRETS_FILE.exists():
        raise CredentialsUnavailable(
            f"Google Calendar OAuth client secrets file not found at {CLIENT_SECRETS_FILE}.\n"
            "To set up Google Calendar access:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project (or select existing)\n"
            "3. Enable 'Google Calendar API'\n"
            "4. Go to Credentials -> Create OAuth 2.0 Client ID\n"
            "5. Choose 'Desktop app' as application type\n"
            f"6. Download the JSON and save it as: {CLIENT_SECRETS_FILE}\n"
            f"Then run: {reauth}")

    if not interactive:
        raise CredentialsUnavailable(
            f"No usable Google Calendar token{f' for account {account!r}' if account else ''}. "
            f"Re-authenticate with: {reauth}")

    from google_auth_oauthlib.flow import InstalledAppFlow
    label = f" ({account})" if account else ""
    print(f"Authenticating{label}... A browser window will open.")
    print("Sign in with the correct Google account!")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    write_private_text(token_file, creds.to_json())
    print(f"Credentials saved to {token_file}", file=sys.stderr)
    return creds


def list_events(calendar_id='primary', days=1, account=None):
    """List events from Google Calendar."""
    from googleapiclient.discovery import build

    creds = get_credentials(account)
    service = build('calendar', 'v3', credentials=creds)

    now = datetime.utcnow()
    time_min = now.isoformat() + 'Z'
    time_max = (now + timedelta(days=days)).isoformat() + 'Z'

    print(f"\nEvents from {calendar_id} (account: {account or 'default'}) for the next {days} day(s):\n")

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=20,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])

    if not events:
        print('No upcoming events found.')
        return []

    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        print(f"  {start[:16]:16} | {event['summary']}")
        if 'location' in event:
            print(f"                   | Location: {event['location']}")

    return events


def list_calendars(account=None):
    """List available calendars."""
    from googleapiclient.discovery import build

    creds = get_credentials(account)
    service = build('calendar', 'v3', credentials=creds)

    print(f"\nAvailable calendars (account: {account or 'default'}):\n")

    calendars_result = service.calendarList().list().execute()
    calendars = calendars_result.get('items', [])

    for cal in calendars:
        primary = " (primary)" if cal.get('primary') else ""
        print(f"  {cal['summary']}{primary}")
        print(f"    ID: {cal['id']}")

    return calendars


def test_connection(account=None):
    """Test the Google Calendar connection."""
    from googleapiclient.discovery import build

    try:
        creds = get_credentials(account)
        service = build('calendar', 'v3', credentials=creds)

        calendars = service.calendarList().list(maxResults=1).execute()
        print(f"Successfully connected (account: {account or 'default'})!")
        print(f"  Found {len(calendars.get('items', []))} calendar(s)")
        return True
    except CredentialsUnavailable:
        raise
    except Exception as e:
        print(f"Connection failed: {e}")
        return False


def main(argv=None):
    """Command-line entry point. The only place in this module that exits."""
    import argparse

    parser = argparse.ArgumentParser(description="Google Calendar Auth Helper")
    parser.add_argument("command", choices=["setup", "test", "list", "calendars"],
                       help="Command to run")
    parser.add_argument("--account", default=None,
                       help="Named account (default: uses default token)")
    parser.add_argument("--calendar", default="primary",
                       help="Calendar ID (default: primary)")
    parser.add_argument("--days", type=int, default=1,
                       help="Number of days to show (default: 1)")
    args = parser.parse_args(argv)

    try:
        if args.command == "setup":
            label = f" for account '{args.account}'" if args.account else ""
            print(f"Setting up Google Calendar authentication{label}...")
            get_credentials(args.account, interactive=True)
            print(f"\nAuthentication complete!")

        elif args.command == "test":
            test_connection(args.account)

        elif args.command == "list":
            list_events(args.calendar, args.days, args.account)

        elif args.command == "calendars":
            list_calendars(args.account)
    except CredentialsUnavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
