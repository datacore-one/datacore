"""
Google Calendar Adapter for External Sync.

DIP-0010: External Sync Architecture - Phase 3

Syncs calendar.org entries with Google Calendar events.

Usage:
    from sync.adapters.calendar import GoogleCalendarAdapter

    adapter = GoogleCalendarAdapter()
    if adapter.is_configured():
        events = adapter.pull_changes()
"""

import os
import pickle
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    OrgCalendarEntry,
    OrgEntry,
    ExternalTaskRef,
    TaskChange,
    SyncResult,
    ChangeType,
    TaskSyncAdapter,
)


# Credentials paths
CREDS_DIR = Path(__file__).parent.parent.parent.parent / "env" / "credentials"
TOKEN_FILE = CREDS_DIR / "google_calendar_token.pickle"
CLIENT_SECRETS_FILE = CREDS_DIR / "google_calendar_client_secret.json"

SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events',
]


@dataclass
class CalendarEvent:
    """Represents a Google Calendar event."""
    id: str
    title: str
    start: datetime
    end: Optional[datetime]
    url: str
    created_at: datetime
    updated_at: datetime

    # Optional fields
    description: str = ""
    location: str = ""
    attendees: List[str] = field(default_factory=list)
    recurring: bool = False
    all_day: bool = False

    # Raw data
    raw: Dict[str, Any] = field(default_factory=dict)


class GoogleCalendarAdapter(TaskSyncAdapter):
    """
    Google Calendar adapter for syncing calendar.org with Google Calendar.

    Maps:
        OrgCalendarEntry (calendar.org) <-> Google Calendar Event
    """

    def __init__(self, calendar_id: str = "primary", config: Dict = None):
        """
        Initialize the adapter.

        Args:
            calendar_id: Google Calendar ID (default: "primary")
            config: Optional configuration dict
        """
        self.calendar_id = calendar_id
        self.config = config or {}
        self._service = None
        self._credentials = None

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def org_file(self) -> str:
        return "calendar.org"

    def is_configured(self) -> bool:
        """Check if adapter is properly configured."""
        return CLIENT_SECRETS_FILE.exists() and TOKEN_FILE.exists()

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Google Calendar."""
        try:
            service = self._get_service()
            if not service:
                return False, "Could not connect to Google Calendar"

            # Try to get calendar info
            calendar = service.calendars().get(calendarId=self.calendar_id).execute()
            return True, f"Connected to: {calendar.get('summary', self.calendar_id)}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def _get_credentials(self):
        """Get valid user credentials."""
        if self._credentials:
            return self._credentials

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = None

        if TOKEN_FILE.exists():
            with open(TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Save refreshed token
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
            else:
                return None

        self._credentials = creds
        return creds

    def _get_service(self):
        """Get Google Calendar API service."""
        if self._service:
            return self._service

        creds = self._get_credentials()
        if not creds:
            return None

        from googleapiclient.discovery import build
        self._service = build('calendar', 'v3', credentials=creds)
        return self._service

    def pull_changes(self, since: Optional[datetime] = None) -> List[TaskChange]:
        """
        Fetch events from Google Calendar.

        Args:
            since: Only fetch events updated after this time

        Returns:
            List of TaskChange objects
        """
        service = self._get_service()
        if not service:
            return []

        changes = []

        # Default: get events for next 14 days
        now = datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + timedelta(days=14)).isoformat() + 'Z'

        try:
            events_result = service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=100,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            for event in events:
                cal_event = self._parse_event(event)
                org_entry = self._event_to_org_entry(cal_event)

                change = TaskChange(
                    change_type=ChangeType.UPDATED,
                    external_task=None,  # We use org_task for calendar
                    org_task=None,
                    timestamp=cal_event.updated_at,
                )
                # Store the org entry in a custom attribute
                change.calendar_entry = org_entry
                changes.append(change)

        except Exception as e:
            print(f"Error pulling calendar events: {e}")

        return changes

    def pull_events(self, days: int = 14) -> List[OrgCalendarEntry]:
        """
        Pull events as OrgCalendarEntry objects.

        Args:
            days: Number of days to look ahead

        Returns:
            List of OrgCalendarEntry objects
        """
        service = self._get_service()
        if not service:
            return []

        entries = []

        now = datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + timedelta(days=days)).isoformat() + 'Z'

        try:
            events_result = service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=100,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            for event in events_result.get('items', []):
                cal_event = self._parse_event(event)
                org_entry = self._event_to_org_entry(cal_event)
                entries.append(org_entry)

        except Exception as e:
            print(f"Error pulling calendar events: {e}")

        return entries

    def push_changes(self, changes: List[TaskChange]) -> SyncResult:
        """Push changes to Google Calendar."""
        result = SyncResult(success=True)

        # TODO: Implement push (create/update events)
        # For now, calendar is read-only

        return result

    def create_task(self, task) -> Optional[ExternalTaskRef]:
        """Create event in Google Calendar."""
        if not isinstance(task, OrgCalendarEntry):
            return None

        service = self._get_service()
        if not service:
            return None

        try:
            event_body = self._org_entry_to_event(task)

            event = service.events().insert(
                calendarId=self.calendar_id,
                body=event_body
            ).execute()

            return ExternalTaskRef(
                adapter="calendar",
                external_id=f"calendar:{self.calendar_id}/{event['id']}",
                url=event.get('htmlLink', '')
            )
        except Exception as e:
            print(f"Error creating calendar event: {e}")
            return None

    def update_task(self, ref: ExternalTaskRef, task) -> bool:
        """Update event in Google Calendar."""
        if not isinstance(task, OrgCalendarEntry):
            return False

        service = self._get_service()
        if not service:
            return False

        try:
            # Extract event ID from external_id
            event_id = ref.external_id.split('/')[-1]

            event_body = self._org_entry_to_event(task)

            service.events().update(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=event_body
            ).execute()

            return True
        except Exception as e:
            print(f"Error updating calendar event: {e}")
            return False

    def close_task(self, ref: ExternalTaskRef) -> bool:
        """Delete event from Google Calendar."""
        service = self._get_service()
        if not service:
            return False

        try:
            event_id = ref.external_id.split('/')[-1]

            service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()

            return True
        except Exception as e:
            print(f"Error deleting calendar event: {e}")
            return False

    def find_matching_task(self, task) -> Optional[ExternalTaskRef]:
        """Find matching event by title and time."""
        if not isinstance(task, OrgCalendarEntry):
            return None

        if not task.timestamp:
            return None

        service = self._get_service()
        if not service:
            return None

        try:
            # Search around the event time
            time_min = (task.timestamp - timedelta(hours=1)).isoformat() + 'Z'
            time_max = (task.timestamp + timedelta(hours=1)).isoformat() + 'Z'

            events_result = service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                q=task.title,  # Search by title
                maxResults=10,
                singleEvents=True
            ).execute()

            for event in events_result.get('items', []):
                if event.get('summary', '').lower() == task.title.lower():
                    return ExternalTaskRef(
                        adapter="calendar",
                        external_id=f"calendar:{self.calendar_id}/{event['id']}",
                        url=event.get('htmlLink', '')
                    )

        except Exception as e:
            print(f"Error searching calendar: {e}")

        return None

    def _parse_event(self, event: Dict) -> CalendarEvent:
        """Parse Google Calendar event to CalendarEvent."""
        # Parse start time
        start_data = event.get('start', {})
        if 'dateTime' in start_data:
            start = datetime.fromisoformat(start_data['dateTime'].replace('Z', '+00:00'))
            all_day = False
        else:
            start = datetime.strptime(start_data.get('date', ''), '%Y-%m-%d')
            all_day = True

        # Parse end time
        end_data = event.get('end', {})
        end = None
        if 'dateTime' in end_data:
            end = datetime.fromisoformat(end_data['dateTime'].replace('Z', '+00:00'))
        elif 'date' in end_data:
            end = datetime.strptime(end_data['date'], '%Y-%m-%d')

        # Parse timestamps
        created = datetime.fromisoformat(
            event.get('created', datetime.now().isoformat()).replace('Z', '+00:00')
        )
        updated = datetime.fromisoformat(
            event.get('updated', datetime.now().isoformat()).replace('Z', '+00:00')
        )

        # Parse attendees
        attendees = [
            a.get('email', '')
            for a in event.get('attendees', [])
        ]

        return CalendarEvent(
            id=event['id'],
            title=event.get('summary', 'Untitled'),
            start=start,
            end=end,
            url=event.get('htmlLink', ''),
            created_at=created,
            updated_at=updated,
            description=event.get('description', ''),
            location=event.get('location', ''),
            attendees=attendees,
            recurring='recurringEventId' in event,
            all_day=all_day,
            raw=event
        )

    def _event_to_org_entry(self, event: CalendarEvent) -> OrgCalendarEntry:
        """Convert CalendarEvent to OrgCalendarEntry."""
        return OrgCalendarEntry(
            id=f"cal-{event.id}",
            title=event.title,
            body=event.description,
            timestamp=event.start,
            end_time=event.end,
            location=event.location,
            attendees=event.attendees,
            external_id=f"calendar:{self.calendar_id}/{event.id}",
            external_url=event.url,
            sync_status="synced",
            sync_updated=datetime.now(),
        )

    def _org_entry_to_event(self, entry: OrgCalendarEntry) -> Dict:
        """Convert OrgCalendarEntry to Google Calendar event body."""
        event = {
            'summary': entry.title,
            'description': entry.body,
        }

        if entry.timestamp:
            if entry.is_all_day:
                event['start'] = {'date': entry.timestamp.strftime('%Y-%m-%d')}
                if entry.end_time:
                    event['end'] = {'date': entry.end_time.strftime('%Y-%m-%d')}
                else:
                    event['end'] = event['start']
            else:
                event['start'] = {'dateTime': entry.timestamp.isoformat()}
                if entry.end_time:
                    event['end'] = {'dateTime': entry.end_time.isoformat()}
                else:
                    # Default 1 hour duration
                    end = entry.timestamp + timedelta(hours=1)
                    event['end'] = {'dateTime': end.isoformat()}

        if entry.location:
            event['location'] = entry.location

        return event

    def sync_to_org_file(self, org_file_path: str, days: int = 14) -> int:
        """
        Sync calendar events to an org file.

        Args:
            org_file_path: Path to the org file
            days: Number of days to sync

        Returns:
            Number of events synced
        """
        entries = self.pull_events(days=days)

        if not entries:
            return 0

        # Generate org content
        lines = [
            "#+TITLE: Calendar",
            "#+FILETAGS: :calendar:",
            "#+STARTUP: overview",
            f"#+LAST_SYNC: [{datetime.now().strftime('%Y-%m-%d %a %H:%M')}]",
            "",
            "* Upcoming Events",
        ]

        for entry in entries:
            lines.extend(self._entry_to_org_lines(entry))

        # Write to file
        with open(org_file_path, 'w') as f:
            f.write('\n'.join(lines))

        return len(entries)

    def _entry_to_org_lines(self, entry: OrgCalendarEntry) -> List[str]:
        """Convert OrgCalendarEntry to org-mode lines."""
        lines = [f"** {entry.title}"]

        # Properties
        lines.append(":PROPERTIES:")
        if entry.external_id:
            lines.append(f":EXTERNAL_ID: {entry.external_id}")
        if entry.external_url:
            lines.append(f":EXTERNAL_URL: [[{entry.external_url}][View in Calendar]]")
        lines.append(f":SYNC_STATUS: {entry.sync_status or 'synced'}")
        lines.append(f":SYNC_UPDATED: [{datetime.now().strftime('%Y-%m-%d %a %H:%M')}]")
        lines.append(":END:")

        # Timestamp
        if entry.timestamp:
            if entry.end_time and not entry.is_all_day:
                # Time range
                start_str = entry.timestamp.strftime('%Y-%m-%d %a %H:%M')
                end_str = entry.end_time.strftime('%H:%M')
                lines.append(f"<{start_str}-{end_str}>")
            elif entry.is_all_day:
                lines.append(f"<{entry.timestamp.strftime('%Y-%m-%d %a')}>")
            else:
                lines.append(f"<{entry.timestamp.strftime('%Y-%m-%d %a %H:%M')}>")

        # Location
        if entry.location:
            lines.append(f"Location: {entry.location}")

        # Attendees
        if entry.attendees:
            lines.append(f"Attendees: {', '.join(entry.attendees)}")

        # Body
        if entry.body:
            lines.append("")
            lines.append(entry.body)

        lines.append("")
        return lines


# CLI interface
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Google Calendar Adapter")
    parser.add_argument("command", choices=["test", "pull", "sync"],
                       help="Command to run")
    parser.add_argument("--calendar", default="primary",
                       help="Calendar ID")
    parser.add_argument("--days", type=int, default=14,
                       help="Days to look ahead")
    parser.add_argument("--output", default="calendar.org",
                       help="Output org file")

    args = parser.parse_args()

    adapter = GoogleCalendarAdapter(calendar_id=args.calendar)

    if args.command == "test":
        success, message = adapter.test_connection()
        print(f"{'✓' if success else '✗'} {message}")

    elif args.command == "pull":
        entries = adapter.pull_events(days=args.days)
        print(f"\nFound {len(entries)} events:\n")
        for entry in entries:
            time_str = entry.timestamp.strftime('%Y-%m-%d %H:%M') if entry.timestamp else 'No time'
            print(f"  {time_str} | {entry.title}")

    elif args.command == "sync":
        count = adapter.sync_to_org_file(args.output, days=args.days)
        print(f"✓ Synced {count} events to {args.output}")
