"""
Google Calendar Authentication Helper.

This script helps set up OAuth credentials for Google Calendar access.
Run this script directly to authenticate with your Google account.

Usage:
    python gcal_auth.py setup    # First-time setup with client credentials
    python gcal_auth.py test     # Test the connection
    python gcal_auth.py list     # List today's events
"""

import os
import pickle
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Credentials storage
CREDS_DIR = Path(__file__).parent.parent.parent.parent / "env" / "credentials"
TOKEN_FILE = CREDS_DIR / "google_calendar_token.pickle"
CLIENT_SECRETS_FILE = CREDS_DIR / "google_calendar_client_secret.json"

SCOPES = ['https://www.googleapis.com/auth/calendar']  # Full read/write access


def get_credentials():
    """Get valid user credentials from storage or run auth flow."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None

    # Load existing token
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials, run auth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
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
                print("\nThen run this script again.")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for next run
        CREDS_DIR.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
        print(f"Credentials saved to {TOKEN_FILE}")

    return creds


def list_events(calendar_id='primary', days=1):
    """List events from Google Calendar."""
    from googleapiclient.discovery import build

    creds = get_credentials()
    service = build('calendar', 'v3', credentials=creds)

    now = datetime.utcnow()
    time_min = now.isoformat() + 'Z'
    time_max = (now + timedelta(days=days)).isoformat() + 'Z'

    print(f"\nEvents from {calendar_id} for the next {days} day(s):\n")

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


def list_calendars():
    """List available calendars."""
    from googleapiclient.discovery import build

    creds = get_credentials()
    service = build('calendar', 'v3', credentials=creds)

    print("\nAvailable calendars:\n")

    calendars_result = service.calendarList().list().execute()
    calendars = calendars_result.get('items', [])

    for cal in calendars:
        primary = " (primary)" if cal.get('primary') else ""
        print(f"  {cal['summary']}{primary}")
        print(f"    ID: {cal['id']}")

    return calendars


def test_connection():
    """Test the Google Calendar connection."""
    from googleapiclient.discovery import build

    try:
        creds = get_credentials()
        service = build('calendar', 'v3', credentials=creds)

        # Try to get calendar list
        calendars = service.calendarList().list(maxResults=1).execute()
        print("✓ Successfully connected to Google Calendar!")
        print(f"  Found {len(calendars.get('items', []))} calendar(s)")
        return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Google Calendar Auth Helper")
    parser.add_argument("command", choices=["setup", "test", "list", "calendars"],
                       help="Command to run")
    parser.add_argument("--calendar", default="primary",
                       help="Calendar ID (default: primary)")
    parser.add_argument("--days", type=int, default=1,
                       help="Number of days to show (default: 1)")

    args = parser.parse_args()

    if args.command == "setup":
        print("Setting up Google Calendar authentication...")
        get_credentials()
        print("\n✓ Authentication complete!")

    elif args.command == "test":
        test_connection()

    elif args.command == "list":
        list_events(args.calendar, args.days)

    elif args.command == "calendars":
        list_calendars()
