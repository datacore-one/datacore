#!/usr/bin/env python3
"""
Campaign Monitor - Polls Late API for analytics, replies, and follower stats.
Writes state to .datacore/state/campaign-state.json for /today hook.

Usage:
  python3 campaign-monitor.py              # Full update
  python3 campaign-monitor.py --replies    # Check replies only

Cron:
  */30 * * * * cd ~/Data && python3 .datacore/modules/comms/lib/campaign-monitor.py --replies
  0 6 * * * cd ~/Data && python3 .datacore/modules/comms/lib/campaign-monitor.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from env_utils import parse_env_file, load_env_files

# Config
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore/state/campaign-state.json"
LATE_BASE = "https://getlate.dev/api/v1"
ORG_ACCOUNT_ID = "YOUR_ACCOUNT_ID"  # Replace with your Late.dev account ID
ORG_PROFILE_ID = "YOUR_PROFILE_ID"  # Replace with your Late.dev profile ID


def load_env():
    """Load LATE_API_KEY from .env file."""
    return parse_env_file(DATA_DIR / ".datacore" / "env" / ".env")


def late_get(endpoint, api_key, params=None):
    """Make a GET request to Late API."""
    url = f"{LATE_BASE}/{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except (URLError, json.JSONDecodeError) as e:
        return {"_error": str(e)}


def load_state():
    """Load existing state or return empty."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    """Save state to file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_analytics(api_key):
    """Fetch post analytics for organization account."""
    data = late_get("analytics", api_key, {
        "profileId": ORG_PROFILE_ID,
        "limit": "20",
        "sortBy": "date",
        "order": "desc"
    })
    if "_error" in data:
        return []

    posts = data.get("posts", [])
    result = []
    for p in posts:
        analytics = p.get("analytics", {})
        result.append({
            "id": p.get("latePostId", p.get("_id")),
            "content": p.get("content", "")[:80],
            "published_at": p.get("publishedAt", ""),
            "permalink": p.get("permalink", ""),
            "impressions": analytics.get("impressions", 0),
            "reach": analytics.get("reach", 0),
            "likes": analytics.get("likes", 0),
            "comments": analytics.get("comments", 0),
            "shares": analytics.get("shares", 0),
            "clicks": analytics.get("clicks", 0),
            "engagement_rate": analytics.get("engagementRate", 0),
        })
    return result


def fetch_follower_stats(api_key):
    """Fetch follower count and growth."""
    data = late_get("accounts/follower-stats", api_key, {
        "accountIds": ORG_ACCOUNT_ID,
        "granularity": "daily"
    })
    if "_error" in data:
        return {"current": 0, "growth": 0, "history": []}

    accounts = data.get("accounts", [])
    stats = data.get("stats", {}).get(ORG_ACCOUNT_ID, [])

    current = accounts[0].get("currentFollowers", 0) if accounts else 0
    growth = accounts[0].get("growth", 0) if accounts else 0

    return {
        "current": current,
        "growth": growth,
        "history": stats[-7:] if stats else []
    }


def fetch_replies(api_key):
    """Fetch posts with comments from Late inbox.

    Note: Late's /inbox/comments endpoint returns posts with comment counts
    but individual comment text is unavailable for Twitter (the sub-endpoint
    /inbox/comments/{id} returns empty). We store comment_count and permalink
    so the user can click through to reply on X directly.
    """
    data = late_get("inbox/comments", api_key, {
        "accountId": ORG_ACCOUNT_ID,
        "limit": "20"
    })
    if "_error" in data:
        return []

    posts_with_comments = []
    for p in data.get("data", []):
        count = p.get("commentCount", 0)
        if count > 0:
            posts_with_comments.append({
                "post_id": p["id"],
                "post_content": p.get("content", "")[:60],
                "comment_count": count,
                "permalink": p.get("permalink", ""),
                "like_count": p.get("likeCount", 0),
            })

    return posts_with_comments


def fetch_scheduled(api_key):
    """Fetch upcoming scheduled posts."""
    data = late_get("posts", api_key, {"status": "scheduled"})
    if "_error" in data:
        return []
    posts = data if isinstance(data, list) else data.get("posts", data.get("data", []))

    result = []
    for p in sorted(posts, key=lambda x: x.get("scheduledFor", "")):
        has_media = len(p.get("mediaItems", [])) > 0
        result.append({
            "id": p.get("_id"),
            "content": p.get("content", "")[:80],
            "scheduled_for": p.get("scheduledFor", ""),
            "status": p.get("status", ""),
            "has_media": has_media,
        })
    return result


def fetch_published(api_key):
    """Fetch recently published posts with their Late status."""
    data = late_get("posts", api_key, {"status": "published"})
    if "_error" in data:
        return []
    posts = data if isinstance(data, list) else data.get("posts", data.get("data", []))

    result = []
    for p in sorted(posts, key=lambda x: x.get("scheduledFor", ""), reverse=True):
        plat = p.get("platforms", [{}])[0]
        has_media = len(p.get("mediaItems", [])) > 0
        result.append({
            "id": p.get("_id"),
            "content": p.get("content", "")[:80],
            "published_at": plat.get("publishedAt", p.get("scheduledFor", "")),
            "has_media": has_media,
        })
    return result


def main():
    env = load_env()
    api_key = env.get("LATE_API_KEY")
    if not api_key:
        print("ERROR: LATE_API_KEY not found in .env")
        sys.exit(1)

    state = load_state()
    replies_only = "--replies" in sys.argv

    if replies_only:
        state["replies"] = fetch_replies(api_key)
        save_state(state)
        total_comments = sum(p.get("comment_count", 0) for p in state.get("replies", []))
        if total_comments:
            print(f"Posts with comments: {len(state['replies'])} ({total_comments} total comments)")
        return

    # Full update
    print("Fetching analytics...")
    state["analytics"] = fetch_analytics(api_key)

    print("Fetching follower stats...")
    state["followers"] = fetch_follower_stats(api_key)

    print("Fetching replies...")
    state["replies"] = fetch_replies(api_key)

    print("Fetching scheduled posts...")
    state["scheduled"] = fetch_scheduled(api_key)

    print("Fetching published posts...")
    state["published"] = fetch_published(api_key)

    save_state(state)

    # Summary
    followers = state.get("followers", {})
    analytics = state.get("analytics", [])
    scheduled = state.get("scheduled", [])
    published = state.get("published", [])
    replies = state.get("replies", [])

    total_comments = sum(p.get("comment_count", 0) for p in replies)

    print(f"\nCampaign State Updated:")
    print(f"  Followers: {followers.get('current', '?')} ({followers.get('growth', 0):+d})")
    print(f"  Published: {len(published)}")
    print(f"  Scheduled: {len(scheduled)}")
    print(f"  Posts with comments: {len(replies)} ({total_comments} comments)")

    if analytics and not isinstance(analytics, dict):
        top = max(analytics, key=lambda x: x.get("engagement_rate", 0))
        if top.get("engagement_rate", 0) > 0:
            print(f"  Top performer: {top['engagement_rate']}% engagement")


if __name__ == "__main__":
    main()
