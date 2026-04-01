#!/usr/bin/env python3
"""Engagement pipeline watchdog — reliability monitor + dead man's switch.

Runs every 30 minutes (07:00-21:00 UTC via systemd timer). Checks that
each service is running on schedule by reading heartbeat timestamps from
watchdog-state.json. Sends Telegram alert naming the service and elapsed
time if any check fails.

Checks:
  - engine: last successful run within 60 minutes (during active hours)
  - analyzer: last successful run within 2 hours (during active hours)
  - X API: single authenticated GET to verify credentials still work
  - reply-queue: alert if oldest entry >4h old AND queue has >10 entries
  - dead man's switch: no post in 24h → Telegram alert
                        no post in 48h → auto-post highest-scored pending

Heartbeat protocol:
  Services write {service, last_ok, ts} to watchdog-state.json at END
  of each successful run. A crash mid-run leaves the timestamp stale.

Usage:
    python3 engagement_watchdog.py           # Run checks, alert if needed
    python3 engagement_watchdog.py --dry-run # Print without sending
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
WATCHDOG_STATE_FILE = DATA_DIR / ".datacore" / "state" / "watchdog-state.json"
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
QUEUE_FILE = DATA_DIR / ".datacore" / "state" / "reply-queue.jsonl"

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(DATA_DIR / ".datacore" / "lib"))

# Stale thresholds
ENGINE_STALE_MINUTES = 60
ANALYZER_STALE_MINUTES = 120

# Reply queue stale: oldest >4h AND count >10 (avoids weekend false alarms)
QUEUE_STALE_HOURS = 4
QUEUE_STALE_MIN_COUNT = 10

# Dead man's switch: no post thresholds
DEAD_MAN_ALERT_HOURS = 24   # Send Telegram alert
DEAD_MAN_AUTOPOST_HOURS = 48  # Auto-post highest-scored pending


def load_env():
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def load_watchdog_state() -> dict:
    if WATCHDOG_STATE_FILE.exists():
        try:
            return json.loads(WATCHDOG_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def write_heartbeat(service: str):
    """Write a heartbeat for this service to watchdog-state.json."""
    WATCHDOG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = load_watchdog_state()
        now = datetime.now(timezone.utc).isoformat()
        state[service] = {"service": service, "last_ok": now, "ts": now}
        WATCHDOG_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"  Heartbeat write failed for {service}: {e}")


def check_service_staleness(state: dict, service: str, max_minutes: int) -> tuple[bool, str]:
    """Check if a service's last_ok timestamp is stale.

    Returns: (is_stale, description)
    """
    entry = state.get(service)
    if not entry:
        return True, f"{service}: no heartbeat found (never ran or state missing)"

    last_ok_str = entry.get("last_ok", "")
    if not last_ok_str:
        return True, f"{service}: heartbeat has no last_ok timestamp"

    try:
        last_ok = datetime.fromisoformat(last_ok_str)
        elapsed = datetime.now(timezone.utc) - last_ok
        elapsed_min = int(elapsed.total_seconds() / 60)

        if elapsed_min > max_minutes:
            elapsed_str = (
                f"{elapsed_min // 60}h {elapsed_min % 60}m"
                if elapsed_min >= 60
                else f"{elapsed_min}m"
            )
            return True, f"{service}: last success {elapsed_str} ago (threshold: {max_minutes}m)"
    except Exception as e:
        return True, f"{service}: could not parse last_ok timestamp ({e})"

    return False, ""


def check_x_api() -> tuple[bool, str]:
    """Verify X API is responding with a single authenticated GET."""
    try:
        from x_poster import XPoster
        poster = XPoster(account="fds", user_id=os.environ.get("FDS_X_USER_ID"))
        # Single lightweight GET — fetch a known static tweet to verify creds
        resp = poster.get_tweet("20")  # @jack's first tweet, always exists
        if resp and resp.get("data"):
            return False, ""
        return True, "X API: responded but returned unexpected data"
    except Exception as e:
        return True, f"X API: call failed — {str(e)[:120]}"


def check_reply_queue() -> tuple[bool, str]:
    """Alert if reply queue has >10 entries AND oldest is >4h old."""
    if not QUEUE_FILE.exists():
        return False, ""

    lines = [l.strip() for l in QUEUE_FILE.read_text().splitlines() if l.strip()]
    count = len(lines)
    if count <= QUEUE_STALE_MIN_COUNT:
        return False, ""

    # Find oldest entry
    now = datetime.now(timezone.utc)
    oldest_age_h = 0
    for line in lines:
        try:
            entry = json.loads(line)
            ts_str = entry.get("queued_at") or entry.get("approved_at") or entry.get("ts", "")
            if ts_str:
                ts = datetime.fromisoformat(ts_str)
                age_h = (now - ts).total_seconds() / 3600
                if age_h > oldest_age_h:
                    oldest_age_h = age_h
        except Exception:
            pass

    if oldest_age_h > QUEUE_STALE_HOURS:
        return True, (
            f"reply-queue: {count} items, oldest {oldest_age_h:.1f}h old "
            f"(Chrome agent may not be running)"
        )
    return False, ""


def send_telegram_alert(message: str):
    """Send a Telegram alert to the engagement chat."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        print("  Cannot send alert: TELEGRAM_BOT_TOKEN or ENGAGEMENT_CHAT_ID not set")
        return

    payload = {
        "chat_id": cid,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    body = json.dumps(payload).encode()
    req = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print("  Alert sent to Telegram")
            else:
                print(f"  Telegram error: {result}")
    except Exception as e:
        print(f"  Alert send failed: {e}")


def check_dead_mans_switch() -> tuple[str, float]:
    """Check when the last tweet was posted.

    Returns: (status, hours_since_last_post)
      status: "ok", "alert" (>24h), "autopost" (>48h), "no_posts"
    """
    if not STATE_FILE.exists():
        return "no_posts", 0

    try:
        import engagement_state as state_mod
        st, _ = state_mod.load(STATE_FILE)
    except Exception:
        st = json.loads(STATE_FILE.read_text())

    posted = st.get("posted", [])
    if not posted:
        return "no_posts", 0

    # Find most recent post
    now = datetime.now(timezone.utc)
    latest = None
    for p in posted:
        try:
            ts = datetime.fromisoformat(p["posted_at"])
            if latest is None or ts > latest:
                latest = ts
        except Exception:
            continue

    if latest is None:
        return "no_posts", 0

    hours = (now - latest).total_seconds() / 3600

    if hours >= DEAD_MAN_AUTOPOST_HOURS:
        return "autopost", hours
    elif hours >= DEAD_MAN_ALERT_HOURS:
        return "alert", hours
    return "ok", hours


def dead_mans_autopost(dry_run: bool = False) -> bool:
    """Auto-post the highest-scored pending draft via X API.

    Called when no post has been made in 48+ hours.
    Returns True if a post was made.
    """
    try:
        import engagement_state as state_mod
        st, baseline = state_mod.load(STATE_FILE)
    except Exception as e:
        print(f"  Dead man's autopost: can't load state: {e}")
        return False

    pending = st.get("pending", [])
    if not pending:
        print("  Dead man's autopost: no pending drafts to post")
        return False

    # Pick the first pending (they're already sorted by engine priority)
    best = pending[0]
    draft = best.get("draft_reply", "")
    target_tweet_id = best.get("target_tweet_id", "")
    author = best.get("target_author", "?")

    if not draft or not target_tweet_id:
        print(f"  Dead man's autopost: best pending has no draft or target_tweet_id")
        return False

    print(f"  Dead man's autopost: posting reply to {author}")
    print(f"    Draft: {draft[:80]}...")

    if dry_run:
        print(f"    [DRY RUN] would post via XPoster")
        return False

    try:
        from x_poster import XPoster
        post_account = best.get('account', 'fds')
        poster = XPoster(account=post_account)
        result = poster.reply(text=draft, reply_to_id=str(target_tweet_id))
        our_tweet_id = result.get("data", {}).get("id", "unknown")
        print(f"    Posted: {our_tweet_id}")

        # Update state: move from pending to posted
        state_mod.approve_pending(st, best["id"], our_tweet_id)

        # Set analyze_at for 24h metrics
        for p in st.get("posted", []):
            if p["id"] == best["id"]:
                p["analyze_at"] = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                p["analyzed"] = False
                p["mode"] = "dead_mans_switch"
                break

        state_mod.save(st, STATE_FILE, baseline=baseline)

        # Notify Telegram
        send_telegram_alert(
            f"🔄 <b>Dead man's switch activated</b>\n"
            f"No posts in 48+ hours — auto-posted:\n"
            f"→ Reply to {author}\n"
            f"<i>{draft[:180]}</i>\n"
            f"<a href='https://x.com/FairDataSociety/status/{our_tweet_id}'>View</a>"
        )
        return True

    except Exception as e:
        print(f"    Autopost failed: {e}")
        return False


def run(dry_run: bool = False):
    """Run all watchdog checks and alert on failures."""
    load_env()
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%H:%M')}] Watchdog checking engagement pipeline...")

    state = load_watchdog_state()
    failures = []

    # Check engine (should run every 30min cycle)
    is_stale, msg = check_service_staleness(state, "engine", ENGINE_STALE_MINUTES)
    if is_stale:
        failures.append(msg)
        print(f"  STALE: {msg}")
    else:
        print(f"  OK: engine")

    # Check analyzer (hourly)
    is_stale, msg = check_service_staleness(state, "analyzer", ANALYZER_STALE_MINUTES)
    if is_stale:
        failures.append(msg)
        print(f"  STALE: {msg}")
    else:
        print(f"  OK: analyzer")

    # Check X API connectivity
    is_down, msg = check_x_api()
    if is_down:
        failures.append(msg)
        print(f"  DOWN: {msg}")
    else:
        print(f"  OK: X API")

    # Check reply queue staleness
    is_stale, msg = check_reply_queue()
    if is_stale:
        failures.append(msg)
        print(f"  STALE: {msg}")
    else:
        print(f"  OK: reply-queue")

    # Dead man's switch: check last post time
    dms_status, hours = check_dead_mans_switch()
    if dms_status == "autopost":
        failures.append(f"dead-man: no post in {hours:.0f}h — attempting auto-post")
        print(f"  CRITICAL: no post in {hours:.0f}h — triggering auto-post")
        dead_mans_autopost(dry_run=dry_run)
    elif dms_status == "alert":
        failures.append(f"dead-man: no post in {hours:.0f}h (auto-post at {DEAD_MAN_AUTOPOST_HOURS}h)")
        print(f"  WARNING: no post in {hours:.0f}h")
    elif dms_status == "no_posts":
        print(f"  INFO: no posted tweets in state (new installation?)")
    else:
        print(f"  OK: dead-man ({hours:.0f}h since last post)")

    if failures:
        lines = [f"⚠️ <b>Engagement watchdog alert</b> — {now.strftime('%H:%M')} UTC\n"]
        for f in failures:
            lines.append(f"• {f}")
        alert = "\n".join(lines)
        print(f"\n{alert}")
        if not dry_run:
            send_telegram_alert(alert)
    else:
        print(f"  All checks passed.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
