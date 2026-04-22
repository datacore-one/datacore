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

# Credentials storage
CREDS_DIR = Path(__file__).parent.parent.parent.parent / "env" / "credentials"
CLIENT_SECRETS_FILE = CREDS_DIR / "google_calendar_client_secret.json"

SCOPES = ['https://www.googleapis.com/auth/calendar']  # Full read/write access

# Default token (backwards compatible)
_DEFAULT_TOKEN = CREDS_DIR / "google_calendar_token.json"
_LEGACY_PICKLE_FILE = CREDS_DIR / "google_calendar_token.pickle"


def _token_file_for(account=None):
    """Get token file path for a named account."""
    if not account or account == "default":
        return _DEFAULT_TOKEN
    return CREDS_DIR / f"google_calendar_token_{account}.json"


def _migrate_pickle_token():
    """Migrate legacy pickle token to JSON format if needed."""
    if _LEGACY_PICKLE_FILE.exists() and not _DEFAULT_TOKEN.exists():
        import pickle
        try:
            with open(_LEGACY_PICKLE_FILE, 'rb') as f:
                creds = pickle.load(f)
            CREDS_DIR.mkdir(parents=True, exist_ok=True)
            _DEFAULT_TOKEN.write_text(creds.to_json())
            _LEGACY_PICKLE_FILE.rename(_LEGACY_PICKLE_FILE.with_suffix('.pickle.bak'))
            print(f"Migrated token from pickle to JSON: {_DEFAULT_TOKEN}")
        except Exception as e:
            print(f"WARNING: Failed to migrate pickle token: {e}")


def get_credentials(account=None):
    """Get valid user credentials from storage or run auth flow."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    token_file = _token_file_for(account)

    # Migrate legacy pickle for default account only
    if not account or account == "default":
        _migrate_pickle_token()

    creds = None

    if token_file.exists():
        try:
            token_data = json.loads(token_file.read_text())
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            print(f"WARNING: Failed to load token JSON: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"ERROR: OAuth token refresh failed: {e}")
                print(f"\nTo re-authenticate:")
                print(f"  1. Delete: {token_file}")
                print(f"  2. Re-run: python {__file__} setup --account {account or 'default'}")
                sys.exit(1)
        else:
            if not CLIENT_SECRETS_FILE.exists():
                print(f"ERROR: Client secrets file not found at {CLIENT_SECRETS_FILE}")
                print("\nTo set up Google Calendar access:")
                print("1. Go to https://console.cloud.google.com/")
                print("2. Create a project (or select existing)")
                print("3. Enable 'Google Calendar API'")
                print("4. Go to Credentials → Create OAuth 2.0 Client ID")
                print("5. Choose 'Desktop app' as application type")
                print("6. Download the JSON and save it as:")
                print(f"   {CLIENT_SECRETS_FILE}")
                sys.exit(1)

            label = f" ({account})" if account else ""
            print(f"Authenticating{label}... A browser window will open.")
            print("Sign in with the correct Google account!")

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        CREDS_DIR.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json())
        print(f"Credentials saved to {token_file}")

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
    except Exception as e:
        print(f"Connection failed: {e}")
        return False


if __name__ == "__main__":
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

    args = parser.parse_args()

    if args.command == "setup":
        label = f" for account '{args.account}'" if args.account else ""
        print(f"Setting up Google Calendar authentication{label}...")
        get_credentials(args.account)
        print(f"\nAuthentication complete!")

    elif args.command == "test":
        test_connection(args.account)

    elif args.command == "list":
        list_events(args.calendar, args.days, args.account)

    elif args.command == "calendars":
        list_calendars(args.account)
