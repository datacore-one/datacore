#!/usr/bin/env python3
"""Runner: newsfeed monitor. Called by systemd timer.

Fetches home timeline via X API v2 and processes through NewsfeedMonitor.
"""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

# Load env
env_file = DATA_DIR / ".datacore" / "env" / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

from newsfeed_monitor import NewsfeedMonitor
from follow_db import FollowDB
from x_poster import XPoster

# Kill switch check (XPoster checks on post, but we check early for likes)
kill_switch = DATA_DIR / ".datacore" / "state" / "campaign-kill-switch"
if kill_switch.exists():
    print(f"Kill switch active: {kill_switch.read_text().strip()}")
    sys.exit(0)

# Fetch home timeline via OAuth 1.0a (user context required for this endpoint)
user_id = os.environ.get("FDS_X_USER_ID")
if not user_id:
    print("Missing FDS_X_USER_ID")
    sys.exit(1)

poster = XPoster(account='fds', user_id=user_id)
url = (
    f"https://api.x.com/2/users/{user_id}/timelines/reverse_chronological"
    f"?max_results=50&tweet.fields=public_metrics,author_id"
)

try:
    data = poster._oauth_get(url)
    tweets = data.get('data', [])
except Exception as e:
    print(f"Timeline fetch failed: {e}")
    sys.exit(1)

if not tweets:
    print("No tweets in timeline")
    sys.exit(0)

# Process
db = FollowDB(DATA_DIR / ".datacore" / "state" / "follow-list.db")
monitor = NewsfeedMonitor(poster=poster, follow_db=db)
result = monitor.process_timeline(tweets)
print(f"Processed: {result['processed']} | Liked: {result['liked']}")
if result['quote_rt_drafts']:
    print(f"Quote-RT drafts: {len(result['quote_rt_drafts'])}")
    for d in result['quote_rt_drafts']:
        print(f"  - {d['text'][:80]}...")
db.close()
