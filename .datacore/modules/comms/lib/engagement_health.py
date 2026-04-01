#!/usr/bin/env python3
"""Engagement Health Dashboard — always-current campaign analytics.

Reads from engagement-state.json, reply-queue.jsonl, and
engagement-errors.jsonl. No new API calls required.

Sections:
  - Today's activity (posted/escalated/rejected vs limits, quotas)
  - 7-day rolling performance (engagement scores from analyzed posts)
  - Quality gate summary (eval pass rate, error rate)
  - Auto-generated alerts

Usage:
    python3 engagement_health.py              # Print markdown
    python3 engagement_health.py --telegram   # Send to Telegram
    python3 engagement_health.py --today-hook # Short block for /today
    python3 engagement_health.py --dry-run    # Print without sending
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
QUEUE_FILE = DATA_DIR / ".datacore" / "state" / "reply-queue.jsonl"
ERRORS_FILE = DATA_DIR / ".datacore" / "state" / "engagement-errors.jsonl"

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(DATA_DIR / ".datacore" / "lib"))

# Engagement score formula — must match engagement_learner._engagement_score()
# likes + replies*3 + impressions/100
def _engagement_score(metrics: dict) -> float:
    return (
        metrics.get("like_count", 0)
        + metrics.get("reply_count", 0) * 3
        + metrics.get("impression_count", 0) / 100
    )


# Default limits (may be overridden by settings.local.yaml)
DEFAULT_DAILY_LIMIT = 85
DEFAULT_ESCALATION_MAX = 5
TOPIC_QUOTA_TARGETS = {
    "privacy_arch": 25,
    "surveillance": 20,
    "fair_data": 20,
    "regulatory": 15,
    "ai_data": 15,
    "infrastructure": 5,
    "fds_mentions": 10,
}

# Viral alert thresholds — can be overridden in settings.local.yaml
DEFAULT_VIRAL_LIKES = 50
DEFAULT_VIRAL_IMPRESSIONS = 5000


def load_env():
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"posted": [], "pending": [], "daily_stats": {}, "daily_quotas": {}}


def _get_queue_depth() -> tuple[int, float]:
    """Return (count, oldest_age_hours) for reply-queue.jsonl."""
    if not QUEUE_FILE.exists():
        return 0, 0.0
    lines = [l.strip() for l in QUEUE_FILE.read_text().splitlines() if l.strip()]
    count = len(lines)
    if count == 0:
        return 0, 0.0

    now = datetime.now(timezone.utc)
    oldest_age_h = 0.0
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
    return count, oldest_age_h


def _get_recent_errors(hours: int = 24) -> list:
    """Return error entries from the last N hours."""
    if not ERRORS_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    errors = []
    for line in ERRORS_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts_str = entry.get("ts", "")
            if ts_str:
                ts = datetime.fromisoformat(ts_str)
                if ts > cutoff:
                    errors.append(entry)
        except Exception:
            pass
    return errors


def _get_7day_posts(st: dict) -> list:
    """Return analyzed posted entries from the last 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    posts = []
    for p in st.get("posted", []):
        if not p.get("analyzed") or not p.get("metrics_24h"):
            continue
        try:
            posted_at = datetime.fromisoformat(p.get("posted_at", ""))
            if posted_at > cutoff:
                posts.append(p)
        except Exception:
            pass
    return posts


def _get_prev_week_posts(st: dict) -> list:
    """Return analyzed posts from 7-14 days ago for week-over-week delta."""
    now = datetime.now(timezone.utc)
    cutoff_start = now - timedelta(days=14)
    cutoff_end = now - timedelta(days=7)
    posts = []
    for p in st.get("posted", []):
        if not p.get("analyzed") or not p.get("metrics_24h"):
            continue
        try:
            posted_at = datetime.fromisoformat(p.get("posted_at", ""))
            if cutoff_start < posted_at <= cutoff_end:
                posts.append(p)
        except Exception:
            pass
    return posts


def build_dashboard(st: dict) -> str:
    """Build full markdown dashboard."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    lines = [f"# Engagement Health — {today}\n"]

    # ── Today's Activity ──────────────────────────────────────────────────────
    lines.append("## Today's Activity\n")
    today_stats = st.get("daily_stats", {}).get(today, {})
    posted = today_stats.get("posted", 0) + today_stats.get("auto_posted", 0)
    escalated = today_stats.get("escalated", 0)
    rejected = today_stats.get("eval_rejected", 0) + today_stats.get("principles_rejected", 0)
    daily_limit = st.get("config", {}).get("daily_reply_limit", DEFAULT_DAILY_LIMIT)
    esc_max = st.get("config", {}).get("escalation_max_per_day", DEFAULT_ESCALATION_MAX)

    lines.append(f"- **Posted**: {posted}/{daily_limit}")
    lines.append(f"- **Escalated**: {escalated}/{esc_max}")
    lines.append(f"- **Rejected**: {rejected}")
    lines.append(f"- **Pending**: {len(st.get('pending', []))}")

    # Topic distribution
    quotas = st.get("daily_quotas", {})
    today_topics = quotas.get("topics", {}) if quotas.get("date") == today else {}
    if today_topics:
        lines.append("\n**Topic distribution** (vs quota):")
        for topic, count in sorted(today_topics.items(), key=lambda x: -x[1]):
            target = TOPIC_QUOTA_TARGETS.get(topic, 0)
            pct = int(count / target * 100) if target > 0 else 0
            flag = " ⚠️ OVER" if pct >= 130 else ""
            lines.append(f"  - {topic}: {count}/{target} ({pct}%){flag}")

    # Reply type mix
    today_reply_types = quotas.get("reply_types", {}) if quotas.get("date") == today else {}
    if today_reply_types:
        total_types = sum(today_reply_types.values()) or 1
        lines.append("\n**Reply type mix**:")
        for rtype, count in sorted(today_reply_types.items(), key=lambda x: -x[1]):
            pct = int(count / total_types * 100)
            flag = " ⚠️ DOMINANT" if pct > 40 else ""
            lines.append(f"  - {rtype}: {count} ({pct}%){flag}")

    # ── 7-Day Rolling Performance ─────────────────────────────────────────────
    lines.append("\n## 7-Day Rolling Performance\n")
    week_posts = _get_7day_posts(st)
    prev_week_posts = _get_prev_week_posts(st)

    if not week_posts:
        lines.append("*No analyzed data yet — available ~24h after first posts are analyzed.*")
    else:
        scores = [_engagement_score(p["metrics_24h"]) for p in week_posts]
        avg_score = sum(scores) / len(scores) if scores else 0
        avg_likes = sum(p["metrics_24h"].get("like_count", 0) for p in week_posts) / len(week_posts)
        avg_impressions = sum(p["metrics_24h"].get("impression_count", 0) for p in week_posts) / len(week_posts)

        # Week-over-week delta
        if prev_week_posts:
            prev_scores = [_engagement_score(p["metrics_24h"]) for p in prev_week_posts]
            prev_avg = sum(prev_scores) / len(prev_scores) if prev_scores else 0
            delta = avg_score - prev_avg
            delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f} vs last week)"
        else:
            delta_str = ""

        lines.append(f"- **Avg engagement score**: {avg_score:.1f}{delta_str}")
        lines.append(f"- **Avg likes**: {avg_likes:.1f}")
        lines.append(f"- **Avg impressions**: {avg_impressions:.0f}")
        lines.append(f"- **Analyzed replies**: {len(week_posts)}")

        # Best/worst by topic
        topic_scores: dict[str, list] = {}
        for p in week_posts:
            t = p.get("topic_group", "unknown")
            topic_scores.setdefault(t, []).append(_engagement_score(p["metrics_24h"]))
        topic_avgs = {t: sum(v) / len(v) for t, v in topic_scores.items() if v}
        if topic_avgs:
            best_topic = max(topic_avgs, key=topic_avgs.get)
            worst_topic = min(topic_avgs, key=topic_avgs.get)
            lines.append(f"- **Best topic**: {best_topic} ({topic_avgs[best_topic]:.1f} avg score)")
            lines.append(f"- **Worst topic**: {worst_topic} ({topic_avgs[worst_topic]:.1f} avg score)")

        # Best/worst by reply type
        type_scores: dict[str, list] = {}
        for p in week_posts:
            rt = p.get("reply_type", "")
            if rt:
                type_scores.setdefault(rt, []).append(_engagement_score(p["metrics_24h"]))
        type_avgs = {rt: sum(v) / len(v) for rt, v in type_scores.items() if v}
        if type_avgs:
            best_type = max(type_avgs, key=type_avgs.get)
            worst_type = min(type_avgs, key=type_avgs.get)
            lines.append(f"- **Best reply type**: {best_type} ({type_avgs[best_type]:.1f})")
            lines.append(f"- **Worst reply type**: {worst_type} ({type_avgs[worst_type]:.1f})")

    # ── Quality Gate Summary ──────────────────────────────────────────────────
    lines.append("\n## Quality Gate Summary\n")
    all_posts = [p for p in st.get("posted", []) if p.get("analyzed")]
    recent_errors = _get_recent_errors(24)

    # Eval pass rate from today's stats
    drafted = today_stats.get("drafted", 0)
    eval_rej = today_stats.get("eval_rejected", 0)
    eval_err = today_stats.get("eval_error", 0)
    if drafted > 0:
        pass_rate = int((drafted - eval_rej - eval_err) / drafted * 100)
        lines.append(f"- **Eval pass rate** (today): {pass_rate}% ({drafted} drafted, {eval_rej} rejected)")
    else:
        lines.append(f"- **Eval pass rate** (today): no data")

    # Consensus score from analyzed posts this week
    week_consensuses = [p.get("eval_consensus", 0) for p in week_posts if p.get("eval_consensus")]
    if week_consensuses:
        avg_consensus = sum(week_consensuses) / len(week_consensuses)
        lines.append(f"- **Avg consensus** (7d): {avg_consensus:.0%}")

    lines.append(f"- **Draft errors** (24h): {len(recent_errors)}")

    # ── Alerts ────────────────────────────────────────────────────────────────
    alerts = _generate_alerts(st, today_stats, today_topics, type_avgs if week_posts else {}, recent_errors)
    if alerts:
        lines.append("\n## Alerts\n")
        for alert in alerts:
            lines.append(alert)

    return "\n".join(lines)


def _generate_alerts(st: dict, today_stats: dict, today_topics: dict, type_avgs: dict, recent_errors: list) -> list:
    """Generate alert strings based on current state."""
    alerts = []
    now = datetime.now(timezone.utc)

    # No posts in last N hours (only during active window 07:00-21:00 UTC)
    hour = now.hour
    if 7 <= hour <= 21:
        last_post_age_h = None
        for p in sorted(st.get("posted", []), key=lambda x: x.get("posted_at", ""), reverse=True):
            try:
                posted_at = datetime.fromisoformat(p.get("posted_at", ""))
                last_post_age_h = (now - posted_at).total_seconds() / 3600
                break
            except Exception:
                pass
        if last_post_age_h is not None and last_post_age_h > 2:
            alerts.append(f"⚠️ No posts in last {last_post_age_h:.0f}h (during active window)")
        elif last_post_age_h is None:
            alerts.append("⚠️ No posts found in state")

    # Reply queue staleness
    queue_count, queue_oldest_h = _get_queue_depth()
    if queue_count > 10 and queue_oldest_h > 4:
        alerts.append(f"⚠️ Reply queue: {queue_count} items, oldest {queue_oldest_h:.1f}h old (Chrome agent may not be running)")

    # Topic buckets over 130%
    for topic, count in today_topics.items():
        target = TOPIC_QUOTA_TARGETS.get(topic, 0)
        if target > 0 and count >= target * 1.3:
            alerts.append(f"⚠️ {topic} bucket at {int(count/target*100)}% of quota")

    # Outperforming reply type
    if type_avgs:
        avg_all = sum(type_avgs.values()) / len(type_avgs)
        for rtype, score in type_avgs.items():
            if score > avg_all * 1.3:
                alerts.append(f"📈 {rtype} outperforming average ({score:.1f} vs {avg_all:.1f} avg)")

    # Eval errors in last 24h
    if len(recent_errors) > 0:
        alerts.append(f"🔴 {len(recent_errors)} eval errors in last 24h")

    return alerts


def build_today_hook(st: dict = None) -> str:
    """Return a compact markdown section for /today briefing."""
    if st is None:
        st = _load_state()

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    today_stats = st.get("daily_stats", {}).get(today, {})
    week_posts = _get_7day_posts(st)

    posted = today_stats.get("posted", 0) + today_stats.get("auto_posted", 0)
    escalated = today_stats.get("escalated", 0)
    pending = len(st.get("pending", []))
    queue_count, queue_oldest_h = _get_queue_depth()

    lines = ["## X Engagement\n"]
    lines.append(f"- **Posted today**: {posted}")
    lines.append(f"- **Escalated**: {escalated} (pending: {pending})")

    if week_posts:
        scores = [_engagement_score(p["metrics_24h"]) for p in week_posts]
        avg_score = sum(scores) / len(scores) if scores else 0
        lines.append(f"- **7d avg engagement score**: {avg_score:.1f}")

    if queue_count > 0:
        lines.append(f"- **Reply queue**: {queue_count} items ({queue_oldest_h:.1f}h old)")

    if pending > 0:
        lines.append(f"\n**⚡ Action required**: {pending} escalated replies in Telegram")

    recent_errors = _get_recent_errors(24)
    if recent_errors:
        lines.append(f"- **Errors (24h)**: {len(recent_errors)} — check engagement-errors.jsonl")

    return "\n".join(lines)


def build_telegram_summary(st: dict) -> str:
    """Build a condensed Telegram-ready HTML summary."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    today_stats = st.get("daily_stats", {}).get(today, {})
    week_posts = _get_7day_posts(st)

    posted = today_stats.get("posted", 0) + today_stats.get("auto_posted", 0)
    escalated = today_stats.get("escalated", 0)
    rejected = today_stats.get("eval_rejected", 0) + today_stats.get("principles_rejected", 0)
    pending = len(st.get("pending", []))
    queue_count, queue_oldest_h = _get_queue_depth()
    recent_errors = _get_recent_errors(24)

    lines = [f"<b>📊 Engagement Health — {today}</b>\n"]
    lines.append(f"Posted: <b>{posted}</b> | Escalated: {escalated} | Rejected: {rejected}")

    if week_posts:
        scores = [_engagement_score(p["metrics_24h"]) for p in week_posts]
        avg_score = sum(scores) / len(scores) if scores else 0
        lines.append(f"7d avg engagement: <b>{avg_score:.1f}</b> ({len(week_posts)} analyzed)")

    if pending > 0:
        lines.append(f"⚡ {pending} escalations pending review")

    if queue_count > 0:
        lines.append(f"Queue: {queue_count} items ({queue_oldest_h:.1f}h old)")

    alerts = _generate_alerts(
        st,
        today_stats,
        st.get("daily_quotas", {}).get("topics", {}) if st.get("daily_quotas", {}).get("date") == today else {},
        {},
        recent_errors,
    )
    for alert in alerts:
        lines.append(alert)

    return "\n".join(lines)


def send_telegram(text: str):
    """Send a message to the engagement Telegram chat."""
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        print("ERROR: TELEGRAM_BOT_TOKEN or ENGAGEMENT_CHAT_ID not set")
        return

    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    body = json.dumps(payload).encode()
    req = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"Sent to Telegram (msg {result['result']['message_id']})")
            else:
                print(f"Telegram error: {result}")
    except Exception as e:
        print(f"Send failed: {e}")


def run(mode: str = "print", dry_run: bool = False):
    """Main entry point.

    Args:
        mode: "print" | "telegram" | "today-hook"
        dry_run: If True, print without sending.
    """
    load_env()

    import engagement_state as state_mod
    st, _ = state_mod.load(STATE_FILE)

    if mode == "today-hook":
        print(build_today_hook(st))
    elif mode == "telegram":
        summary = build_telegram_summary(st)
        if dry_run:
            print(summary)
        else:
            send_telegram(summary)
    else:
        print(build_dashboard(st))


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    if "--telegram" in args:
        run("telegram", dry_run=dry_run)
    elif "--today-hook" in args:
        run("today-hook")
    else:
        run("print")
