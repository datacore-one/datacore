#!/usr/bin/env python3
"""Schedule tweets from calendar markdown files via Late API.

Reads week/month tweet schedule files, parses content and scheduled times,
and submits to Late API for automated posting.

Usage:
    python3 content_scheduler.py <calendar.md> [--dry-run] [--force-past]

The markdown format expected:
    ## N. Day Mon DD — ~HH:MM UTC — Title
    > Tweet content line 1
    > Tweet content line 2

For threads, each tweet is a ### heading under the ## section.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Add lib to path
LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(LIB_DIR.parent.parent.parent / "lib"))

from env_utils import load_env_files

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))


def load_env():
    """Load env vars from .env files."""
    load_env_files()


def parse_calendar(md_path: str, year: int = None) -> List[dict]:
    """Parse a tweet calendar markdown file into scheduled items.

    Returns list of:
        {
            'title': str,
            'scheduled_time': str (ISO 8601),
            'content': str (tweet text),
            'is_thread': bool,
            'thread_tweets': [str] (if thread),
            'card': str or None,
        }
    """
    year = year or datetime.now().year
    text = Path(md_path).read_text()
    items = []

    # Match ## sections: "## N. Day Mon DD — ~HH:MM UTC — Title"
    section_pattern = re.compile(
        r'^## \d+\.\s+'
        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
        r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+)\s+'
        r'—\s+~?(\d{2}:\d{2})\s+UTC\s+—\s+(.+)$',
        re.MULTILINE,
    )

    sections = list(section_pattern.finditer(text))
    for i, match in enumerate(sections):
        date_str = match.group(1)  # "Mar 4"
        time_str = match.group(2)  # "09:00"
        title = match.group(3).strip()

        # Parse date
        try:
            dt = datetime.strptime(f"{date_str} {year} {time_str}", "%b %d %Y %H:%M")
            dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"  WARNING: Could not parse date '{date_str} {time_str}', skipping")
            continue

        # Extract section content (between this ## and next ##)
        start = match.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        section_text = text[start:end]

        # Check for card
        card_match = re.search(r'\*\*Card\*\*:\s*(\S+)', section_text)
        card = card_match.group(1) if card_match else None
        if card == '—' or card == 'none':
            card = None

        # Check for thread (### sub-headings)
        thread_parts = re.split(r'^### .+$', section_text, flags=re.MULTILINE)

        if len(thread_parts) > 2:
            # It's a thread
            tweets = []
            for part in thread_parts[1:]:  # Skip first (before first ###)
                tweet_text = _extract_blockquote(part)
                if tweet_text:
                    tweets.append(tweet_text)
            if tweets:
                items.append({
                    'title': title,
                    'scheduled_time': dt.isoformat(),
                    'content': tweets[0],  # First tweet for Late API
                    'is_thread': True,
                    'thread_tweets': tweets,
                    'card': card,
                })
        else:
            # Single tweet
            tweet_text = _extract_blockquote(section_text)
            if tweet_text:
                items.append({
                    'title': title,
                    'scheduled_time': dt.isoformat(),
                    'content': tweet_text,
                    'is_thread': False,
                    'thread_tweets': [],
                    'card': card,
                })

    return items


def _extract_blockquote(text: str) -> Optional[str]:
    """Extract blockquote content (lines starting with >)."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('>'):
            content = line[1:].strip()
            lines.append(content)

    if not lines:
        return None

    # Join with newlines, collapse empty lines
    result = '\n'.join(lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def schedule_via_late(items: List[dict], dry_run: bool = False,
                      force_past: bool = False) -> dict:
    """Schedule parsed items via Late API.

    Returns: {'scheduled': N, 'skipped_past': N, 'skipped_thread': N, 'errors': N}
    """
    from late_api_wrapper import LateAPIClient

    api_key = os.environ.get("LATE_API_KEY")
    if not api_key:
        raise ValueError("LATE_API_KEY not set")

    client = LateAPIClient(api_key, verify_links=True)
    now = datetime.now(timezone.utc)

    stats = {'scheduled': 0, 'skipped_past': 0, 'skipped_thread': 0, 'errors': 0}

    for item in items:
        scheduled = datetime.fromisoformat(item['scheduled_time'])
        is_past = scheduled < now

        print(f"\n{'[PAST] ' if is_past else ''}#{items.index(item)+1}: {item['title']}")
        print(f"  Time: {scheduled.strftime('%a %b %d %H:%M UTC')}")
        print(f"  Content ({len(item['content'])} chars): {item['content'][:80]}...")

        if is_past and not force_past:
            print(f"  SKIPPED (past)")
            stats['skipped_past'] += 1
            continue

        if item['is_thread']:
            print(f"  SKIPPED (thread — {len(item['thread_tweets'])} tweets, Late API doesn't support threads)")
            stats['skipped_thread'] += 1
            continue

        if dry_run:
            print(f"  [DRY RUN] Would schedule via Late API")
            stats['scheduled'] += 1
            continue

        try:
            result = client.create_post(
                content=item['content'],
                platforms=['twitter'],
                scheduled_time=item['scheduled_time'],
                skip_verification=not bool(re.search(r'https?://', item['content'])),
            )
            post_id = result.get('post', {}).get('_id', '?')
            print(f"  SCHEDULED (Late post {post_id})")
            stats['scheduled'] += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            stats['errors'] += 1

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Schedule tweets from calendar markdown')
    parser.add_argument('calendar', help='Path to calendar markdown file')
    parser.add_argument('--dry-run', action='store_true', help='Parse and show, do not schedule')
    parser.add_argument('--force-past', action='store_true', help='Schedule past items too')
    parser.add_argument('--year', type=int, default=2026, help='Year for date parsing')

    args = parser.parse_args()

    load_env()

    print(f"Parsing: {args.calendar}")
    items = parse_calendar(args.calendar, year=args.year)
    print(f"Found {len(items)} scheduled items")

    if not items:
        print("No items to schedule.")
        sys.exit(0)

    stats = schedule_via_late(items, dry_run=args.dry_run, force_past=args.force_past)
    print(f"\n=== Summary ===")
    print(f"Scheduled: {stats['scheduled']}")
    print(f"Skipped (past): {stats['skipped_past']}")
    print(f"Skipped (thread): {stats['skipped_thread']}")
    print(f"Errors: {stats['errors']}")
