"""
Create recurring events on a Google Calendar.

The standard GoogleCalendarAdapter only handles single events — this helper
adds RRULE recurrence support for blocking review/cadence slots that should
repeat weekly or monthly.

Usage:
    python .datacore/lib/sync/calendar_create_recurring.py preview
    python .datacore/lib/sync/calendar_create_recurring.py insert
    python .datacore/lib/sync/calendar_create_recurring.py insert --account swarm

Edit MANIFEST below to change which events get created.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ADAPTER_DIR = Path(__file__).resolve().parent / "adapters"
sys.path.insert(0, str(ADAPTER_DIR))

from gcal_auth import get_credentials  # noqa: E402

TIMEZONE = "Europe/Ljubljana"


def event(title: str, start: str, end: str, rrule: str,
          description: str = "", location: str = "") -> dict:
    """Build an event manifest entry. Times are local to TIMEZONE (no Z suffix)."""
    return {
        "title": title,
        "start": start,
        "end": end,
        "rrule": rrule,
        "description": description,
        "location": location,
    }


# First occurrence anchors (Europe/Ljubljana, no UTC offset — Google applies TZ).
# Anchored to the week of 2026-05-11 (Mon).
MANIFEST: List[dict] = [
    # --- Firm cadence ---
    event(
        title="Datafund Weekly",
        start="2026-05-11T14:00:00",
        end="2026-05-11T14:45:00",
        rrule="RRULE:FREQ=WEEKLY;BYDAY=MO",
        description="Weekly Datafund sync — pipeline, pilots, decisions.",
    ),
    event(
        title="1:1 Tadej",
        start="2026-05-12T14:00:00",
        end="2026-05-12T14:30:00",
        rrule="RRULE:FREQ=WEEKLY;BYDAY=TU",
        description="Weekly 1:1 with Tadej.",
    ),

    # --- Ventures (solo) ---
    event(
        title="Venture Portfolio Pulse",
        start="2026-05-11T09:30:00",
        end="2026-05-11T09:50:00",
        rrule="RRULE:FREQ=WEEKLY;BYDAY=MO",
        description="Solo. Read overnight ventures.cadences output, decide interventions per venture (Forge / Meridian / Megaphone).",
    ),
    event(
        title="Hypothesis Board Review",
        start="2026-05-15T13:30:00",
        end="2026-05-15T14:00:00",
        rrule="RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR",
        description="Solo, bi-weekly. Per venture: validate / retire / spawn hypotheses.",
    ),

    # --- Friday Review Block ---
    event(
        title="/retro — Engineering Retrospective",
        start="2026-05-15T14:30:00",
        end="2026-05-15T14:50:00",
        rrule="RRULE:FREQ=WEEKLY;BYDAY=FR",
        description="Run /retro. Commit history, code quality patterns.",
    ),
    event(
        title="/weekly-trading-review",
        start="2026-05-15T15:00:00",
        end="2026-05-15T15:25:00",
        rrule="RRULE:FREQ=WEEKLY;BYDAY=FR",
        description="Run /weekly-trading-review. Adherence + key trades — not deep analysis.",
    ),
    event(
        title="/gtd-weekly-review",
        start="2026-05-15T15:30:00",
        end="2026-05-15T16:30:00",
        rrule="RRULE:FREQ=WEEKLY;BYDAY=FR",
        description="Run /gtd-weekly-review. Cornerstone steps: inbox empty, projects, WAITING, deadlines, priorities.",
    ),

    # --- Monthly ---
    event(
        title="/gtd-monthly-strategic",
        start="2026-05-29T16:30:00",  # last Friday of May 2026
        end="2026-05-29T17:15:00",
        rrule="RRULE:FREQ=MONTHLY;BYDAY=-1FR",
        description="START / STOP / CONTINUE per work area. Runs after Friday review block on the last Friday.",
    ),
    event(
        title="/monthly-performance — Trading",
        start="2026-06-01T09:15:00",  # first weekday of June 2026 (Mon)
        end="2026-06-01T10:00:00",
        rrule="RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1",
        description="Run /monthly-performance — comprehensive monthly trading metrics.",
    ),
    event(
        title="/monthly-plan — Comms Content Calendar",
        start="2026-06-01T16:00:00",
        end="2026-06-01T16:45:00",
        rrule="RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1",
        description="Run /monthly-plan — generate next month's content calendar from quarterly campaign strategy.",
    ),
]


def build_event_body(spec: dict) -> dict:
    body = {
        "summary": spec["title"],
        "description": spec.get("description", ""),
        "start": {"dateTime": spec["start"], "timeZone": TIMEZONE},
        "end": {"dateTime": spec["end"], "timeZone": TIMEZONE},
        "recurrence": [spec["rrule"]],
        "reminders": {"useDefault": True},
    }
    if spec.get("location"):
        body["location"] = spec["location"]
    return body


def get_service(account: Optional[str] = None):
    from googleapiclient.discovery import build

    creds = get_credentials(account=account)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def preview() -> None:
    print(f"Will create {len(MANIFEST)} recurring events on calendar.")
    print(f"Timezone: {TIMEZONE}\n")
    for i, spec in enumerate(MANIFEST, 1):
        print(f"  {i:2}. {spec['title']}")
        print(f"      first: {spec['start']} → {spec['end']}")
        print(f"      rrule: {spec['rrule']}\n")


def insert(account: Optional[str] = None, calendar_id: str = "primary") -> int:
    service = get_service(account=account)
    created = 0
    for spec in MANIFEST:
        body = build_event_body(spec)
        try:
            ev = service.events().insert(
                calendarId=calendar_id,
                body=body,
                sendUpdates="none",  # solo events; no attendees to notify
            ).execute()
            link = ev.get("htmlLink", "")
            print(f"OK  {spec['title']}  →  {link}")
            created += 1
        except Exception as exc:
            print(f"ERR {spec['title']}: {exc}")
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Create recurring events on Google Calendar")
    parser.add_argument("command", choices=["preview", "insert"])
    parser.add_argument("--account", default=None,
                        help="Named account from gcal_auth (default: gregor@datafund.io)")
    parser.add_argument("--calendar", default="primary",
                        help="Calendar ID (default: primary)")
    args = parser.parse_args()

    if args.command == "preview":
        preview()
        return 0

    if args.command == "insert":
        print(f"Inserting {len(MANIFEST)} events into '{args.calendar}' "
              f"(account={args.account or 'default'})  TZ={TIMEZONE}\n")
        n = insert(account=args.account, calendar_id=args.calendar)
        print(f"\nDone. Created {n}/{len(MANIFEST)} events.")
        print(f"Run timestamp: {datetime.now().isoformat()}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
