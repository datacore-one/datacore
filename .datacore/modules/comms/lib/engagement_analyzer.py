#!/usr/bin/env python3
"""Fetch 24hr metrics for posted engagement replies and store results.

Runs via nightshift timer. Finds posted replies where:
  - analyze_at is set and in the past
  - analyzed is not True
  - our_tweet_id is a real tweet ID

Fetches public_metrics (likes, replies, impressions, bookmarks, quotes, retweets)
via X API v2 and stores them on the posted entry. Prints a summary report.

Usage:
    python3 engagement_analyzer.py [--dry-run]
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
STATE_FILE = DATA_DIR / ".datacore/state/engagement-state.json"
REPORT_DIR = DATA_DIR / ".datacore/state/engagement-reports"

sys.path.insert(0, str(DATA_DIR / ".datacore/modules/comms/lib"))
sys.path.insert(0, str(DATA_DIR / ".datacore/lib"))

# Viral alert thresholds (can be overridden in settings.local.yaml)
VIRAL_LIKES_THRESHOLD = int(os.environ.get("VIRAL_LIKES_THRESHOLD", "50"))
VIRAL_IMPRESSIONS_THRESHOLD = int(os.environ.get("VIRAL_IMPRESSIONS_THRESHOLD", "5000"))


def load_env():
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def is_real_tweet_id(tid: str) -> bool:
    return bool(tid and tid not in ("unknown", "pending_chrome", "", "None")
                and str(tid).isdigit() and len(str(tid)) >= 10)


def fetch_metrics(tweet_id: str) -> dict:
    """Fetch public metrics for a tweet via X API v2."""
    from x_poster import XPoster
    poster = XPoster(account="fds", user_id=os.environ.get("FDS_X_USER_ID"))
    fields = "public_metrics,created_at,conversation_id"
    resp = poster.get_tweet(tweet_id, fields=fields)
    data = resp.get("data", {})
    metrics = data.get("public_metrics", {})
    return {
        "like_count": metrics.get("like_count", 0),
        "reply_count": metrics.get("reply_count", 0),
        "retweet_count": metrics.get("retweet_count", 0),
        "quote_count": metrics.get("quote_count", 0),
        "bookmark_count": metrics.get("bookmark_count", 0),
        "impression_count": metrics.get("impression_count", 0),
    }


def fetch_fds_follower_count() -> int | None:
    """Fetch current @FairDataSociety follower count via X API."""
    try:
        from x_poster import XPoster
        poster = XPoster(account="fds", user_id=os.environ.get("FDS_X_USER_ID"))
        user_id = os.environ.get("FDS_X_USER_ID")
        if not user_id:
            return None
        url = f"https://api.x.com/2/users/{user_id}?user.fields=public_metrics"
        resp = poster._oauth_get(url)
        metrics = resp.get("data", {}).get("public_metrics", {})
        return metrics.get("followers_count")
    except Exception as e:
        print(f"  Follower count fetch failed: {e}")
        return None


def find_our_reply_id(target_tweet_id: str, posted_at: str) -> str | None:
    """Search FDS user timeline to find our reply to a given tweet.

    Used to retroactively resolve 'pending_chrome' our_tweet_ids.
    Returns tweet ID string or None.
    """
    try:
        from x_poster import XPoster
        import os as _os
        poster = XPoster(account="fds", user_id=_os.environ.get("FDS_X_USER_ID"))
        user_id = _os.environ.get("FDS_X_USER_ID")
        if not user_id:
            return None
        # Fetch recent tweets from FDS timeline
        url = (
            f"https://api.x.com/2/users/{user_id}/tweets"
            f"?max_results=100"
            f"&tweet.fields=conversation_id,in_reply_to_user_id,referenced_tweets,created_at"
            f"&expansions=referenced_tweets.id"
        )
        resp = poster._oauth_get(url)
        tweets = resp.get("data", [])
        for t in tweets:
            refs = t.get("referenced_tweets", [])
            for ref in refs:
                if ref.get("type") == "replied_to" and ref.get("id") == target_tweet_id:
                    return t["id"]
        return None
    except Exception as e:
        print(f"  Timeline lookup failed: {e}")
        return None


def _send_viral_alert(p: dict, metrics: dict):
    """Send a Telegram alert for a viral reply."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        return

    likes = metrics.get("like_count", 0)
    impressions = metrics.get("impression_count", 0)
    our_tweet_id = p.get("our_tweet_id", "")
    author = p.get("target_author", "?")
    draft = p.get("draft_reply", "")[:140]
    url = f"https://x.com/FairDataSociety/status/{our_tweet_id}" if our_tweet_id else ""

    text = (
        f"🚀 <b>Viral reply alert!</b>\n"
        f"Reply to {author}\n"
        f"<i>{draft}</i>\n"
        f"Likes: <b>{likes}</b> | Impressions: <b>{impressions:,}</b>\n"
    )
    if url:
        text += f"<a href='{url}'>View reply</a>"

    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    body = json.dumps(payload).encode()

    from urllib.request import Request as _Req, urlopen as _urlopen
    req = _Req(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with _urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"    Viral alert sent for {p.get('id')}")
    except Exception as e:
        print(f"    Viral alert send failed: {e}")


def run(dry_run: bool = False):
    """Analyze posted replies — fetch 24hr metrics from X API.

    Lock strategy: NO long-held lock. state_mod.save() handles its own
    lock-and-merge, so we just load → do API work → save. This prevents
    blocking the autonomous engine (which also needs the state lock).
    """
    load_env()
    now = datetime.now(timezone.utc)

    import engagement_state as state_mod
    st, baseline = state_mod.load(STATE_FILE)
    posted = st.get("posted", [])

    due = []
    for p in posted:
        if p.get("analyzed"):
            continue
        analyze_at = p.get("analyze_at")
        if not analyze_at:
            continue
        try:
            at = datetime.fromisoformat(analyze_at)
            if at > now:
                continue
        except Exception:
            continue
        due.append(p)

    print(f"Found {len(due)} replies due for analysis")
    if not due:
        return

    results = []
    for p in due:
        tweet_id = p.get("our_tweet_id", "")
        draft_id = p["id"]
        author = p.get("target_author", "?")

        # Resolve pending_chrome IDs retroactively
        if not is_real_tweet_id(tweet_id):
            target_id = p.get("target_tweet_id", "")
            if target_id and is_real_tweet_id(target_id):
                print(f"  Resolving tweet ID for {draft_id} ({author})...")
                found = find_our_reply_id(target_id, p.get("posted_at", ""))
                if found:
                    p["our_tweet_id"] = found
                    tweet_id = found
                    print(f"    Resolved: {found}")
                else:
                    print(f"    Not found — marking analyzed with no metrics")
                    p["analyzed"] = True
                    p["analyze_error"] = "tweet_id_not_found"
                    results.append({"draft_id": draft_id, "author": author, "error": "not_found"})
                    continue
            else:
                p["analyzed"] = True
                p["analyze_error"] = "no_target_tweet_id"
                continue

        print(f"  Fetching metrics for {draft_id} ({author}) tweet {tweet_id}...")
        try:
            metrics = fetch_metrics(tweet_id)
            p["metrics_24h"] = metrics
            p["analyzed"] = True
            p["analyzed_at"] = now.isoformat()
            results.append({"draft_id": draft_id, "author": author, **metrics})
            m = metrics
            print(f"    likes={m['like_count']} replies={m['reply_count']} impressions={m['impression_count']}")

            # Viral alert — only if not already alerted for this tweet
            if not dry_run:
                alerted = st.get("viral_alerted", [])
                tweet_key = tweet_id or draft_id
                is_viral = (
                    m.get("like_count", 0) >= VIRAL_LIKES_THRESHOLD
                    or m.get("impression_count", 0) >= VIRAL_IMPRESSIONS_THRESHOLD
                )
                if is_viral and tweet_key not in alerted:
                    print(f"    VIRAL: {m['like_count']} likes, {m['impression_count']} impressions")
                    _send_viral_alert(p, m)
                    alerted.append(tweet_key)
                    st["viral_alerted"] = alerted[-200:]  # trim to last 200
        except Exception as e:
            print(f"    Error: {e}")
            p["analyze_error"] = str(e)

    if not dry_run:
        state_mod.save(st, STATE_FILE, baseline=baseline)
        print(f"\nSaved {len(due)} analyzed entries to state")

    if results:
        _write_report(results, now)

    # Daily follower count snapshot (once per day)
    if not dry_run:
        today = now.strftime("%Y-%m-%d")
        snapshots = st.get("follower_snapshots", [])
        already_today = any(s.get("date") == today for s in snapshots)
        if not already_today:
            count = fetch_fds_follower_count()
            if count is not None:
                snapshots.append({"date": today, "followers": count})
                snapshots = snapshots[-365:]  # Keep 1 year of daily snapshots
                st["follower_snapshots"] = snapshots
                state_mod.save(st, STATE_FILE)
                print(f"  Follower snapshot: {count:,} followers on {today}")

    # Write heartbeat so watchdog knows analyzer ran successfully
    if not dry_run:
        try:
            from engagement_watchdog import write_heartbeat
            write_heartbeat("analyzer")
        except Exception as e:
            print(f"  Heartbeat write failed: {e}")


def _write_report(results: list, now: datetime):
    """Write a markdown report of 24hr metrics."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    report_file = REPORT_DIR / f"reply-metrics-{date_str}.md"

    # Append to existing file if it exists
    header = f"\n## Analysis run: {now.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
    header += "| Author | Likes | Replies | Retweets | Impressions | Bookmarks |\n"
    header += "|--------|-------|---------|----------|-------------|----------|\n"

    rows = []
    for r in results:
        if "error" in r:
            rows.append(f"| {r['author']} | — | — | — | — | — |  *(id not found)*")
        else:
            rows.append(
                f"| {r['author']} | {r.get('like_count',0)} | {r.get('reply_count',0)} "
                f"| {r.get('retweet_count',0)} | {r.get('impression_count',0)} "
                f"| {r.get('bookmark_count',0)} |"
            )

    content = header + "\n".join(rows) + "\n"

    with open(report_file, "a") as f:
        f.write(content)

    print(f"\nReport written: {report_file}")
    print(content)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
