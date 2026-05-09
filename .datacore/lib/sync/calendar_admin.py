"""
Google Calendar admin helper — find / delete / patch / add events.

Companion to calendar_create_recurring.py. Use this for ad-hoc edits
to existing events (move time, retire a slot, drop in a one-off cadence).

Examples:
    python .datacore/lib/sync/calendar_admin.py find "/retro"
    python .datacore/lib/sync/calendar_admin.py find "/retro" --first-id
    python .datacore/lib/sync/calendar_admin.py delete <event_id>
    python .datacore/lib/sync/calendar_admin.py patch <event_id> \\
        --start 2026-05-11T10:15:00 --end 2026-05-11T10:35:00
    python .datacore/lib/sync/calendar_admin.py add --title "/foo" \\
        --start 2026-05-11T09:00:00 --end 2026-05-11T10:00:00 \\
        --rrule "RRULE:FREQ=WEEKLY;BYDAY=MO" --description "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ADAPTER_DIR = Path(__file__).resolve().parent / "adapters"
sys.path.insert(0, str(ADAPTER_DIR))

from gcal_auth import get_credentials  # noqa: E402

TIMEZONE = "Europe/Ljubljana"


def get_service(account: Optional[str] = None):
    from googleapiclient.discovery import build

    creds = get_credentials(account=account)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def find_events(service, calendar_id: str, title: str, days_ahead: int = 60):
    """Find recurring master events by title (substring match, case-insensitive)."""
    now = datetime.utcnow()
    time_min = now.isoformat() + "Z"
    time_max = (now + timedelta(days=days_ahead)).isoformat() + "Z"

    out = []
    page_token = None
    needle = title.lower()

    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=False,  # get masters
            maxResults=250,
            pageToken=page_token,
        ).execute()

        for ev in resp.get("items", []):
            summary = ev.get("summary", "")
            if needle in summary.lower():
                out.append({
                    "id": ev["id"],
                    "summary": summary,
                    "start": ev.get("start", {}),
                    "recurrence": ev.get("recurrence", []),
                    "status": ev.get("status"),
                    "htmlLink": ev.get("htmlLink", ""),
                })

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return out


def delete_event(service, calendar_id: str, event_id: str) -> None:
    service.events().delete(
        calendarId=calendar_id,
        eventId=event_id,
        sendUpdates="none",
    ).execute()


def patch_event(service, calendar_id: str, event_id: str,
                start: Optional[str], end: Optional[str],
                rrule: Optional[str] = None) -> dict:
    body = {}
    if start:
        body["start"] = {"dateTime": start, "timeZone": TIMEZONE}
    if end:
        body["end"] = {"dateTime": end, "timeZone": TIMEZONE}
    if rrule:
        body["recurrence"] = [rrule]
    return service.events().patch(
        calendarId=calendar_id,
        eventId=event_id,
        body=body,
        sendUpdates="none",
    ).execute()


def add_event(service, calendar_id: str, title: str,
              start: str, end: str, rrule: Optional[str] = None,
              description: str = "", location: str = "") -> dict:
    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start, "timeZone": TIMEZONE},
        "end": {"dateTime": end, "timeZone": TIMEZONE},
        "reminders": {"useDefault": True},
    }
    if rrule:
        body["recurrence"] = [rrule]
    if location:
        body["location"] = location
    return service.events().insert(
        calendarId=calendar_id,
        body=body,
        sendUpdates="none",
    ).execute()


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Calendar admin helper")
    parser.add_argument("--account", default=None)
    parser.add_argument("--calendar", default="primary")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find")
    p_find.add_argument("title")
    p_find.add_argument("--first-id", action="store_true",
                        help="Print only the first matching event ID (for shell use)")
    p_find.add_argument("--days", type=int, default=60)

    p_del = sub.add_parser("delete")
    p_del.add_argument("event_id")

    p_patch = sub.add_parser("patch")
    p_patch.add_argument("event_id")
    p_patch.add_argument("--start")
    p_patch.add_argument("--end")
    p_patch.add_argument("--rrule")

    p_add = sub.add_parser("add")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--start", required=True)
    p_add.add_argument("--end", required=True)
    p_add.add_argument("--rrule")
    p_add.add_argument("--description", default="")
    p_add.add_argument("--location", default="")

    args = parser.parse_args()
    service = get_service(account=args.account)

    if args.cmd == "find":
        events = find_events(service, args.calendar, args.title, days_ahead=args.days)
        if args.first_id:
            if not events:
                print("", end="")
                return 1
            print(events[0]["id"])
            return 0
        print(json.dumps(events, indent=2, default=str))
        return 0

    if args.cmd == "delete":
        delete_event(service, args.calendar, args.event_id)
        print(f"OK  deleted {args.event_id}")
        return 0

    if args.cmd == "patch":
        ev = patch_event(service, args.calendar, args.event_id,
                         args.start, args.end, args.rrule)
        print(f"OK  patched {ev['id']}  →  {ev.get('htmlLink', '')}")
        return 0

    if args.cmd == "add":
        ev = add_event(service, args.calendar, args.title,
                       args.start, args.end, args.rrule,
                       args.description, args.location)
        print(f"OK  added {ev['id']}  →  {ev.get('htmlLink', '')}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
