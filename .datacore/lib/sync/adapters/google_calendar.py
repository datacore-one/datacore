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

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from credential_files import calendar_token_path, write_private_text
from file_utils import atomic_write_text, file_lock

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
TOKEN_FILE = CREDS_DIR / "google_calendar_token.json"
_LEGACY_PICKLE_FILE = CREDS_DIR / "google_calendar_token.pickle"
CLIENT_SECRETS_FILE = CREDS_DIR / "google_calendar_client_secret.json"

SCOPES = ['https://www.googleapis.com/auth/calendar']  # Match gcal_auth.py — single broad scope


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

    Supports multiple Google accounts via named tokens.
    Maps:
        OrgCalendarEntry (calendar.org) <-> Google Calendar Event
    """

    def __init__(self, calendar_id: str = "primary", config: Dict = None, account: str = None):
        """
        Initialize the adapter.

        Args:
            calendar_id: Google Calendar ID (default: "primary")
            config: Optional configuration dict
            account: Named account for multi-account support (uses separate token file)
        """
        self.config = config or {}
        self.calendar_id = self.config.get("calendar_id", calendar_id)
        self.account = self.config.get("account", account)
        if not isinstance(self.calendar_id, str) or not self.calendar_id:
            raise ValueError("calendar_id must be a nonempty string")
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
        return CLIENT_SECRETS_FILE.exists() and self._token_file().exists()

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

    def _migrate_pickle_token(self):
        """Legacy pickle files are retained for manual recovery, never loaded."""
        if _LEGACY_PICKLE_FILE.exists() and not TOKEN_FILE.exists():
            import logging
            logging.warning("Legacy pickle credentials ignored; re-authenticate to create a JSON token. Original preserved.")

    def _token_file(self):
        return calendar_token_path(CREDS_DIR, TOKEN_FILE, self.account)

    def _get_credentials(self):
        with file_lock(self._token_file(), timeout=30):
            return self._credentials_unlocked()

    def _credentials_unlocked(self):
        """Get valid user credentials."""
        if self._credentials:
            return self._credentials

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        import logging

        token_file = self._token_file()

        # Migrate legacy pickle token for default account only
        if not self.account or self.account == "default":
            self._migrate_pickle_token()

        creds = None

        if token_file.exists():
            try:
                token_data = json.loads(token_file.read_text())
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except Exception as e:
                logging.warning(f"Failed to load token JSON: {e}")
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    CREDS_DIR.mkdir(parents=True, exist_ok=True)
                    write_private_text(token_file, creds.to_json())
                except Exception as e:
                    logging.error(
                        f"OAuth token refresh failed for Google Calendar ({self.account or 'default'}): {e}\n"
                        f"Re-authenticate: python gcal_auth.py setup --account {self.account or 'default'}"
                    )
                    return None
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
        changes = []
        for event in self._read_events(14):
            cal_event = self._parse_event(event)
            change = TaskChange(change_type=ChangeType.UPDATED, external_task=None,
                                org_task=None, timestamp=cal_event.updated_at)
            change.calendar_entry = self._event_to_org_entry(cal_event)
            changes.append(change)
        return changes

    def _read_events(self, days: int) -> list[dict]:
        """A complete snapshot or an error, never an acknowledged partial page."""
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
            raise ValueError("calendar days must be between 1 and 366")
        service = self._get_service()
        if not service:
            raise RuntimeError("calendar credentials or service unavailable")
        now = datetime.utcnow()
        args = dict(calendarId=self.calendar_id, timeMin=now.isoformat() + 'Z',
                    timeMax=(now + timedelta(days=days)).isoformat() + 'Z',
                    maxResults=100, singleEvents=True, orderBy='startTime')
        events, tokens = [], set()
        while True:
            page = service.events().list(**args).execute()
            if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                raise ValueError("invalid calendar response")
            events.extend(page.get("items", []))
            token = page.get("nextPageToken")
            if not token:
                return events
            if not isinstance(token, str) or token in tokens:
                raise ValueError("invalid or repeated calendar page token")
            tokens.add(token)
            if len(tokens) >= 1000:
                raise ValueError("calendar pagination limit exceeded")
            args["pageToken"] = token

    def pull_events(self, days: int = 14) -> List[OrgCalendarEntry]:
        return [self._event_to_org_entry(self._parse_event(event))
                for event in self._read_events(days)]

    def push_changes(self, changes: List[TaskChange]) -> SyncResult:
        """Push changes to Google Calendar."""
        result = SyncResult(success=True)

        # No writes were performed; never acknowledge a nonempty batch.
        if changes:
            result.success = False
            result.errors.append("calendar batch push is not implemented")

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
        event_id = self._authorized_event_id(ref)
        if event_id is None:
            return False

        service = self._get_service()
        if not service:
            return False

        try:
            event_body = self._org_entry_to_event(task)

            service.events().patch(
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
        event_id = self._authorized_event_id(ref)
        if event_id is None:
            return False
        service = self._get_service()
        if not service:
            return False

        try:
            service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()

            return True
        except Exception as e:
            print(f"Error deleting calendar event: {e}")
            return False

    def _authorized_event_id(self, ref):
        prefix = f"calendar:{self.calendar_id}/"
        if ref.adapter != "calendar" or not ref.external_id.startswith(prefix):
            return None
        event_id = ref.external_id[len(prefix):]
        return event_id if event_id and "/" not in event_id else None

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
            all_day=event.all_day,
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
                    event['end'] = {'date': (entry.timestamp + timedelta(days=1)).strftime('%Y-%m-%d')}
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
        # Include fetching in the lock: an older slow snapshot must not land
        # after a newer fast one. Lock contention fails without writing.
        with file_lock(Path(org_file_path), timeout=30):
            return self._sync_to_org_file_locked(org_file_path, days)

    def _sync_to_org_file_locked(self, org_file_path: str, days: int) -> int:
        """
        Sync calendar events to an org file.

        Args:
            org_file_path: Path to the org file
            days: Number of days to sync

        Returns:
            Number of events synced
        """
        entries = self.pull_events(days=days)

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

        # Archive previous snapshots before replacing a complete generated view.
        import hashlib
        path = Path(org_file_path)
        try:
            previous = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            previous = None
        if previous is not None:
            digest = hashlib.sha256(previous.encode("utf-8")).hexdigest()
            backup = path.parent / ".calendar-backups" / (path.name + "." + digest)
            if not backup.exists():
                atomic_write_text(backup, previous)
        atomic_write_text(path, '\n'.join(lines) + '\n')

        return len(entries)

    def _entry_to_org_lines(self, entry: OrgCalendarEntry) -> List[str]:
        """Convert OrgCalendarEntry to org-mode lines."""
        from org_literal import prose, scalar
        # A remote title may look like a task keyword or end in an AI tag.
        # The fixed prefix and closing quote keep it a calendar heading.
        lines = [f'** Calendar: "{scalar(entry.title)}"']

        # Properties
        lines.append(":PROPERTIES:")
        if entry.external_id:
            lines.append(f":EXTERNAL_ID: {scalar(entry.external_id)}")
        if entry.external_url:
            lines.append(f":EXTERNAL_URL: {scalar(entry.external_url)}")
        lines.append(f":SYNC_STATUS: {scalar(entry.sync_status or 'synced')}")
        lines.append(f":SYNC_UPDATED: [{datetime.now().strftime('%Y-%m-%d %a %H:%M')}]")
        lines.append(":END:")

        # Timestamp
        if entry.timestamp:
            if entry.end_time and entry.end_time <= entry.timestamp:
                raise ValueError("calendar end must follow start")
            if entry.is_all_day:
                start_str = entry.timestamp.strftime('%Y-%m-%d %a')
                # Google Calendar uses an exclusive end date; Org ranges
                # display inclusive dates. Keep every day of multi-day events.
                last_day = (entry.end_time - timedelta(days=1)) if entry.end_time else entry.timestamp
                if last_day.date() > entry.timestamp.date():
                    lines.append(f"<{start_str}>--<{last_day.strftime('%Y-%m-%d %a')}>")
                else:
                    lines.append(f"<{start_str}>")
            elif entry.end_time and entry.end_time.date() != entry.timestamp.date():
                lines.append(f"<{entry.timestamp.strftime('%Y-%m-%d %a %H:%M')}>--<{entry.end_time.strftime('%Y-%m-%d %a %H:%M')}>")
            elif entry.end_time:
                # Time range
                start_str = entry.timestamp.strftime('%Y-%m-%d %a %H:%M')
                end_str = entry.end_time.strftime('%H:%M')
                lines.append(f"<{start_str}-{end_str}>")
            else:
                lines.append(f"<{entry.timestamp.strftime('%Y-%m-%d %a %H:%M')}>")

        # Location
        if entry.location:
            lines.extend(prose(f"Location: {entry.location}"))

        # Attendees
        if entry.attendees:
            lines.extend(prose(f"Attendees: {', '.join(entry.attendees)}"))

        # Body
        if entry.body:
            lines.append("")
            lines.extend(prose(entry.body))

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
