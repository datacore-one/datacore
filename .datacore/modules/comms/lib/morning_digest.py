#!/usr/bin/env python3
"""Morning Digest — 07:30 UTC daily summary of overnight engagement activity.

Sends a compact Telegram message with:
  - Auto-posted / escalated / auto-rejected counts (last 24h)
  - Top performing reply (most likes+impressions)
  - Topic mix for the day
  - Up to 5 escalated replies awaiting approval
  - Health module data: what's working, error count, queue depth

If kill switch is active: sends digest only, no posting.

Usage:
    python3 morning_digest.py           # Send Telegram digest
    python3 morning_digest.py --dry-run # Print without sending
    python3 morning_digest.py --today-hook # Output markdown section for /today
    python3 morning_digest.py --evening    # Evening summary (19:00 UTC)
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(DATA_DIR / ".datacore" / "lib"))


def load_env():
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _get_yesterday_stats(st: dict) -> dict:
    """Get stats from yesterday's daily_stats entry."""
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")

    # Combine yesterday + today (digest runs at 07:30, covers prior 24h)
    stats = {}
    for day in [yesterday, today]:
        day_stats = st.get("daily_stats", {}).get(day, {})
        for k, v in day_stats.items():
            stats[k] = stats.get(k, 0) + v

    return stats


def _get_top_performer(st: dict) -> dict | None:
    """Find the highest-engagement posted reply from the last 24h."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    best = None
    best_score = -1

    for p in st.get("posted", []):
        try:
            posted_at = datetime.fromisoformat(p.get("posted_at", ""))
        except Exception:
            continue
        if posted_at < cutoff:
            continue

        metrics = p.get("metrics_24h", {})
        score = metrics.get("like_count", 0) + metrics.get("impression_count", 0) // 100
        if score > best_score:
            best_score = score
            best = {**p, "_engagement_score": score}

    return best


def _get_topic_mix(st: dict) -> dict:
    """Get today's topic quota counts."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quotas = st.get("daily_quotas", {})
    if quotas.get("date") != today:
        return {}
    return quotas.get("topics", {})


def _get_pending_escalations(st: dict) -> list:
    """Get pending items that came from escalation (sorted oldest first)."""
    escalated = [
        p for p in st.get("pending", [])
        if (p.get("source", "").startswith("escalated:") or
            "ESCALATED" in p.get("draft_reply", "")[:30])
    ]
    # Sort by discovered_at
    escalated.sort(key=lambda p: p.get("discovered_at", ""))
    return escalated[:5]  # Max 5


def _format_topic_mix(topics: dict) -> str:
    """Format topic mix as a compact string."""
    total = sum(topics.values()) or 1
    parts = []
    for topic, count in sorted(topics.items(), key=lambda x: -x[1]):
        if count > 0:
            pct = int(count / total * 100)
            parts.append(f"{topic.replace('_', ' ')} {pct}%")
    return " | ".join(parts[:5]) if parts else "no data"


def _get_health_extras(st: dict) -> str:
    """Get health module extras: what's working, errors, queue depth.

    Imports engagement_health as a module (not subprocess) to avoid
    subprocess failure modes. Wrapped in try/except so a health module
    error never crashes the digest.
    """
    try:
        import engagement_health as health_mod
        from datetime import timedelta

        # What's working: best topic + reply type from 7d analyzed posts
        week_posts = health_mod._get_7day_posts(st)
        extras = []

        if week_posts:
            topic_scores: dict = {}
            type_scores: dict = {}
            for p in week_posts:
                t = p.get("topic_group", "")
                rt = p.get("reply_type", "")
                score = health_mod._engagement_score(p.get("metrics_24h", {}))
                if t:
                    topic_scores.setdefault(t, []).append(score)
                if rt:
                    type_scores.setdefault(rt, []).append(score)

            if topic_scores:
                best_topic = max(topic_scores, key=lambda t: sum(topic_scores[t]) / len(topic_scores[t]))
                best_topic_avg = sum(topic_scores[best_topic]) / len(topic_scores[best_topic])
                extras.append(f"Best topic: {best_topic} ({best_topic_avg:.1f} score)")

            if type_scores:
                best_type = max(type_scores, key=lambda t: sum(type_scores[t]) / len(type_scores[t]))
                best_type_avg = sum(type_scores[best_type]) / len(type_scores[best_type])
                extras.append(f"Best reply type: {best_type} ({best_type_avg:.1f} score)")

        # Error count
        recent_errors = health_mod._get_recent_errors(24)
        if recent_errors:
            extras.append(f"⚠️ {len(recent_errors)} errors in last 24h")

        # Queue depth
        queue_count, queue_oldest_h = health_mod._get_queue_depth()
        if queue_count > 0:
            extras.append(f"Reply queue: {queue_count} items ({queue_oldest_h:.1f}h old)")

        if extras:
            return "\n" + "\n".join(extras)
    except Exception as e:
        # Health module error must never crash the digest
        return f"\n(health data unavailable: {e})"
    return ""


def build_digest(st: dict) -> tuple[str, list]:
    """Build digest message text and escalated pending items.

    Returns: (html_text, escalated_drafts)
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    stats = _get_yesterday_stats(st)
    auto_posted = stats.get("auto_posted", 0)
    escalated = stats.get("escalated", 0)
    eval_rejected = stats.get("eval_rejected", 0)
    principles_rejected = stats.get("principles_rejected", 0)
    total_rejected = eval_rejected + principles_rejected

    top = _get_top_performer(st)
    topic_mix = _get_topic_mix(st)
    pending_escalations = _get_pending_escalations(st)

    # Kill switch status
    kill_switch = DATA_DIR / ".datacore" / "state" / "campaign-kill-switch"
    kill_active = kill_switch.exists()
    kill_tag = " 🚫 KILL SWITCH ACTIVE" if kill_active else ""

    lines = [
        f"<b>FDS overnight — {date_str}</b>{kill_tag}\n",
        f"Posted: <b>{auto_posted}</b> auto | "
        f"<b>{escalated}</b> escalated | "
        f"<b>{total_rejected}</b> rejected",
    ]

    if top:
        metrics = top.get("metrics_24h", {})
        likes = metrics.get("like_count", 0)
        impressions = metrics.get("impression_count", 0)
        if likes > 0 or impressions > 0:
            our_url = f"https://x.com/FairDataSociety/status/{top.get('our_tweet_id', '')}"
            lines.append(
                f"Top: <a href='{our_url}'>{_escape_html(top.get('target_author', '?'))}</a> "
                f"— {likes} likes, {impressions:,} impressions"
            )

    if topic_mix:
        lines.append(f"Topics: {_escape_html(_format_topic_mix(topic_mix))}")

    # Health module extras (what's working, error count, queue depth)
    health_extras = _get_health_extras(st)
    if health_extras.strip():
        lines.append(_escape_html(health_extras))

    if pending_escalations:
        lines.append(f"\n<b>⚡ {len(pending_escalations)} escalated replies need review:</b>")
        for p in pending_escalations:
            draft_id = p.get("id", "?")
            author = p.get("target_author", "?")
            draft = p.get("draft_reply", "")
            # Strip escalation prefix if present
            if draft.startswith("[ESCALATED:"):
                end = draft.find("]")
                reason = draft[1:end] if end > 0 else "escalated"
                draft = draft[end + 1:].strip()
            else:
                reason = "escalated"

            lines.append(
                f"\n<b>{_escape_html(author)}</b> ({_escape_html(reason[:50])})\n"
                f"<i>{_escape_html(draft[:160])}</i>"
            )
    else:
        lines.append("\nNo escalated replies pending.")

    return "\n".join(lines), pending_escalations


def build_evening_digest(st: dict) -> str:
    """Build evening summary (19:00 UTC) — today's activity + pending items."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    today = date_str
    today_stats = st.get("daily_stats", {}).get(today, {})

    posted = today_stats.get("posted", 0) + today_stats.get("auto_posted", 0)
    escalated_count = today_stats.get("escalated", 0)

    lines = [f"<b>FDS evening — {date_str}</b>\n"]
    lines.append(f"Posted today: <b>{posted}</b> | Escalated: {escalated_count}")

    # Top performer so far today
    top = _get_top_performer(st)
    if top:
        metrics = top.get("metrics_24h", {})
        likes = metrics.get("like_count", 0)
        if likes > 0:
            our_url = f"https://x.com/FairDataSociety/status/{top.get('our_tweet_id', '')}"
            lines.append(
                f"Top so far: <a href='{our_url}'>{_escape_html(top.get('target_author', '?'))}</a>"
                f" — {likes} likes"
            )

    # Reply queue depth
    try:
        import engagement_health as health_mod
        queue_count, queue_oldest_h = health_mod._get_queue_depth()
        if queue_count > 0:
            lines.append(f"Reply queue: {queue_count} items ({queue_oldest_h:.1f}h old)")
    except Exception:
        pass

    # Escalations still pending
    pending_escalations = _get_pending_escalations(st)
    if pending_escalations:
        lines.append(f"\n⚡ {len(pending_escalations)} escalations still pending review")

    return "\n".join(lines)


def build_today_hook(st: dict) -> str:
    """Return a markdown section for /today briefing."""
    stats = _get_yesterday_stats(st)
    auto_posted = stats.get("auto_posted", 0)
    escalated = stats.get("escalated", 0)
    total_rejected = stats.get("eval_rejected", 0) + stats.get("principles_rejected", 0)

    topic_mix = _get_topic_mix(st)
    mix_str = _format_topic_mix(topic_mix) if topic_mix else "no data"

    pending = _get_pending_escalations(st)

    lines = [
        "## Overnight X Engagement\n",
        f"- **Auto-posted**: {auto_posted} replies",
        f"- **Escalated**: {escalated} (pending: {len(pending)})",
        f"- **Rejected**: {total_rejected}",
        f"- **Topics**: {mix_str}",
    ]

    top = _get_top_performer(st)
    if top:
        metrics = top.get("metrics_24h", {})
        likes = metrics.get("like_count", 0)
        if likes > 0:
            lines.append(f"- **Top reply**: {top.get('target_author', '?')} — {likes} likes")

    if pending:
        lines.append(f"\n**⚡ Action required**: {len(pending)} escalated replies in Telegram")

    return "\n".join(lines)


def send_digest(html_text: str, dry_run: bool = False):
    """Send the digest to Telegram."""
    if dry_run:
        print("=== DIGEST (DRY RUN) ===")
        print(html_text.replace("<b>", "**").replace("</b>", "**")
              .replace("<i>", "_").replace("</i>", "_")
              .replace("<a href='", "[").replace("'>", "](").replace("</a>", ")"))
        print("========================")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        print("ERROR: TELEGRAM_BOT_TOKEN or ENGAGEMENT_CHAT_ID not set")
        return

    payload = {
        "chat_id": cid,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"Digest sent (msg {result['result']['message_id']})")
            else:
                print(f"Telegram error: {result}")
    except Exception as e:
        print(f"Send failed: {e}")


def run(dry_run: bool = False, today_hook: bool = False, evening: bool = False):
    """Main entry point."""
    load_env()

    import engagement_state as state_mod
    st, _ = state_mod.load(STATE_FILE)

    if today_hook:
        print(build_today_hook(st))
        return

    if evening:
        html_text = build_evening_digest(st)
        send_digest(html_text, dry_run=dry_run)
        return

    html_text, _ = build_digest(st)
    send_digest(html_text, dry_run=dry_run)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    today_hook = "--today-hook" in sys.argv
    evening = "--evening" in sys.argv
    run(dry_run=dry_run, today_hook=today_hook, evening=evening)
