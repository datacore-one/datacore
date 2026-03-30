#!/usr/bin/env python3
"""Engagement Engine — autonomous posting with escalation tiers.

Discovers conversations, evaluates with principles gate + 4 evaluators,
posts autonomously or escalates to Telegram based on tiers.

Usage:
    python3 .datacore/modules/comms/lib/engagement_engine.py
    python3 .datacore/modules/comms/lib/engagement_engine.py --dry-run
    python3 .datacore/modules/comms/lib/engagement_engine.py --autonomous

Rate Budget (Basic X API tier = 3,000 writes/month):
    - Replies: 85/day
    - Thread tweets: 7/day
    - Original posts: 3/day
    Total: 95 writes/day = 2,850/month (buffer from 3,000 cap)

Escalation Tiers:
    - consensus ≥ 0.65 → auto-post (high-follower accounts get score boost)
    - consensus 0.50-0.65 → escalate to Telegram (max 5/day)
    - consensus < 0.50 OR principles < 0.70 → auto-reject

Content Diversity Quotas (tracked in engagement-state.json):
    Topic targets: privacy_arch 25, surveillance 20, fair_data 20,
                   regulatory 15, ai_data 15, infrastructure 5
    Penalty: -3 score if topic bucket at/above target
    Reply type penalty: -2 if one type > 40% of today's replies
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(LIB_DIR.parent.parent.parent / "lib"))

import engagement_state as state_mod
import engagement_discover as discover_mod
import engagement_draft as draft_mod
import engagement_notify as notify_mod
import engagement_monitor as monitor_mod
from env_utils import load_env_files

# Paths
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"

# Default limits — feed-first model (3 cycles/day, ~$4/day target)
DEFAULT_DAILY_REPLY_LIMIT = 15
DEFAULT_MAX_PER_HOUR = 5      # Per cycle (must fit in 900s timeout)
DEFAULT_ESCALATION_MAX = 5    # Max Telegram escalations per day
AUTONOMOUS_THRESHOLD = 0.70   # consensus ≥ this → auto-post (quality gate)
ESCALATION_THRESHOLD = 0.45   # below this → auto-reject

# Topic quota targets (daily soft limits — feed-first, 15 replies/day)
TOPIC_QUOTA_TARGETS = {
    "ai_agents": 4,       # Primary growth topic
    "ai_data": 3,         # AI + data custody
    "provenance": 3,      # Data provenance / authenticity
    "decentralized_ai": 2,  # Decentralized AI community
    "privacy_arch": 2,    # Core privacy
    "surveillance": 2,    # Surveillance
    "fair_data": 2,       # Fair data economy
    "fds_mentions": 5,    # Always respond to mentions
}

# Reply type quota: max 40% of daily posts from one type
REPLY_TYPE_MAX_PCT = 0.40
REPLY_TYPES = ["agreement", "extension", "question", "experience"]


def load_env():
    """Load env vars from .env files."""
    load_env_files()


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send_info_message(html_text: str):
    """Send a plain info message to the engagement Telegram chat (no buttons)."""
    import json as _json
    from urllib.request import Request as _Req, urlopen as _urlopen

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        return

    payload = {"chat_id": cid, "text": html_text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    body = _json.dumps(payload).encode()
    req = _Req(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with _urlopen(req, timeout=10):
            pass
    except Exception:
        pass  # Best effort


def _get_daily_quotas(st: dict) -> dict:
    """Get or initialize daily quotas, resetting if date changed."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quotas = st.get("daily_quotas", {})

    if quotas.get("date") != today:
        # New day — reset all quotas
        quotas = {
            "date": today,
            "topics": {k: 0 for k in TOPIC_QUOTA_TARGETS},
            "reply_types": {t: 0 for t in REPLY_TYPES},
        }
        st["daily_quotas"] = quotas

    return quotas


def _score_with_quota_penalty(conv: dict, quotas: dict, posted_today: int) -> float:
    """Compute candidate score with diversity quota penalties applied.

    Base score = relevance (1-10).
    Penalties:
      -3 if topic bucket at/above daily target
      -2 if dominant reply type would exceed 40% of today's replies
    """
    relevance = float(conv.get("relevance", 5))

    # Follower reach boost — prefer high-visibility targets for maximum reach
    followers = conv.get("followers", 0) or 0
    if followers > 50_000:
        relevance *= 1.5
    elif followers > 10_000:
        relevance *= 1.2

    # Topic penalty
    topic = conv.get("topic_group", "")
    topic_count = quotas.get("topics", {}).get(topic, 0)
    topic_target = TOPIC_QUOTA_TARGETS.get(topic, 999)
    if topic_count >= topic_target:
        relevance -= 3

    # Note: reply type penalty is applied after drafting (see classify_reply_type)
    # We track the penalty candidate here but can't apply without the draft

    return relevance


def _classify_reply_type(draft_text: str) -> str:
    """Classify a draft reply as one of 4 types using a fast LLM call.

    Returns: "agreement", "extension", "question", or "experience"
    """
    prompt = f"""Classify this tweet reply into exactly ONE of these 4 types:
- agreement: simple amplification/affirmation of the OP's point
- extension: adds one new angle or implication the OP didn't mention
- question: poses a genuine question to advance the thread
- experience: brief first-person account from building privacy infrastructure

Reply to classify:
{draft_text}

Respond with ONLY one word: agreement, extension, question, or experience"""

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))

    try:
        import subprocess
        result = subprocess.run(
            ["claude", "-p", "--model", "haiku", "--output-format", "text",
             "--no-session-persistence", "--max-turns", "1"],
            input=prompt, capture_output=True, text=True,
            cwd=str(DATA_DIR), env=env, timeout=15,
        )
        if result.returncode == 0:
            word = result.stdout.strip().lower().split()[0]
            if word in REPLY_TYPES:
                return word
    except Exception:
        pass
    return "extension"  # Default if classification fails


def _check_reply_type_penalty(reply_type: str, quotas: dict, posted_today: int) -> bool:
    """Return True if adding this reply type would exceed 40% of today's replies."""
    if posted_today < 3:
        return False  # Too few to enforce ratio
    type_count = quotas.get("reply_types", {}).get(reply_type, 0)
    return type_count / posted_today > REPLY_TYPE_MAX_PCT


def _update_quotas(st: dict, topic_group: str, reply_type: str):
    """Increment topic and reply type counters after a successful post."""
    quotas = _get_daily_quotas(st)
    topics = quotas.setdefault("topics", {})
    topics[topic_group] = topics.get(topic_group, 0) + 1

    reply_types = quotas.setdefault("reply_types", {})
    reply_types[reply_type] = reply_types.get(reply_type, 0) + 1

    st["daily_quotas"] = quotas


def _get_escalation_count(st: dict) -> int:
    """Count escalations sent to Telegram today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return st.get("daily_stats", {}).get(today, {}).get("escalated", 0)


def _should_escalate(conv: dict, evaluation) -> tuple[bool, str]:
    """Determine if this draft should go to Telegram rather than auto-posting.

    Returns: (should_escalate, reason)
    """
    consensus = evaluation.consensus if evaluation else 0.65

    # Borderline quality → escalate
    if ESCALATION_THRESHOLD <= consensus < AUTONOMOUS_THRESHOLD:
        return True, f"borderline_consensus ({consensus:.0%})"

    return False, ""


def run(dry_run: bool = False, autonomous: bool = False, no_escalate: bool = False, account: str = 'fds'):
    """Run one cycle of the engagement engine. account: 'fds', 'plur', etc."""
    load_env()
    now = datetime.now(timezone.utc)
    if autonomous and no_escalate:
        mode_str = "autonomous (no-escalate)"
    elif autonomous:
        mode_str = "autonomous"
    else:
        mode_str = "telegram-approval"
    print(f"[{now.strftime('%H:%M')}] Engagement engine starting ({mode_str})...")

    # Kill switch check
    kill_switch = DATA_DIR / ".datacore" / "state" / "campaign-kill-switch"
    if kill_switch.exists():
        print(f"  KILL SWITCH ACTIVE: {kill_switch.read_text().strip()}")
        print(f"  Remove {kill_switch} to resume.")
        return

    # Load state
    st, baseline = state_mod.load(STATE_FILE)

    # Initialize daily quotas (reset if new day)
    quotas = _get_daily_quotas(st)

    # Check daily limits
    posted_today = state_mod.today_count(st, "posted")
    daily_reply_limit = st.get("config", {}).get("daily_reply_limit", DEFAULT_DAILY_REPLY_LIMIT)
    max_per_hour = st.get("config", {}).get("max_per_hour", DEFAULT_MAX_PER_HOUR)
    escalation_max = st.get("config", {}).get("escalation_max_per_day", DEFAULT_ESCALATION_MAX)

    if posted_today >= daily_reply_limit:
        print(f"  Daily limit reached ({posted_today}/{daily_reply_limit}). Skipping.")
        state_mod.save(st, STATE_FILE, baseline=baseline)
        return

    # Clean expired pending
    expired = state_mod.expire_old_pending(st)
    if expired:
        print(f"  Expired {expired} pending drafts")

    # Phase 1: Discover
    print("  Phase 1: Discovering conversations...")
    try:
        conversations = discover_mod.discover(st)
        print(f"  Found {len(conversations)} new conversations")
    except Exception as e:
        print(f"  Discovery failed: {e}", file=sys.stderr)
        state_mod.save(st, STATE_FILE, baseline=baseline)
        return

    # Phase 1b: Monitor replies to our posted tweets
    print("  Phase 1b: Monitoring replies to our tweets...")
    try:
        follow_ups = monitor_mod.monitor(st)
        if follow_ups:
            print(f"  Found {len(follow_ups)} follow-up conversations")
            conversations = follow_ups + conversations  # Prioritise follow-ups
        else:
            print("  No follow-ups found")
    except Exception as e:
        print(f"  Monitor failed: {e}", file=sys.stderr)

    if not conversations:
        print("  No new conversations. Done.")
        state_mod.save(st, STATE_FILE, baseline=baseline)
        return

    # Sort by diversity-adjusted score
    def candidate_score(conv):
        return _score_with_quota_penalty(conv, quotas, posted_today)

    conversations.sort(key=candidate_score, reverse=True)

    # Phase 2+3: Draft, evaluate, and post/escalate
    pending_count = len(st.get("pending", []))
    slots = min(max_per_hour, len(conversations), daily_reply_limit - posted_today)

    # Limit pending queue to avoid flooding Telegram
    max_pending = 30
    if not autonomous:
        slots = min(slots, max_pending - pending_count)

    if slots <= 0:
        print(f"  No slots available (pending={pending_count}). Skipping.")
        state_mod.save(st, STATE_FILE, baseline=baseline)
        return

    # Set up poster for autonomous posting
    # Priority: chrome_poster (bypasses reply restrictions) → XPoster (API fallback)
    poster = None
    poster_type = "none"
    if autonomous and not dry_run:
        # Try chrome_poster first (works with restricted replies)
        try:
            from chrome_poster import post_reply as _chrome_post
            # Quick check: auth state exists
            if (DATA_DIR / ".datacore" / "state" / "x-auth-state.json").exists():
                def poster(tweet_url, text):
                    return _chrome_post(tweet_url, text)
                poster_type = "chrome"
                print("  Chrome poster ready (browser-based)")
            else:
                raise FileNotFoundError("no auth state")
        except Exception as e:
            print(f"  Chrome poster unavailable ({e}), trying XPoster...")
            try:
                from x_poster import XPoster
                _xposter = XPoster(account=account)
                def poster(tweet_url, text):
                    tid = tweet_url.rstrip('/').split('/')[-1]
                    res = _xposter.reply(text=text, reply_to_id=tid)
                    return res.get("data", {}).get("id", "unknown")
                poster_type = "api"
                print("  X API poster ready (fallback)")
            except Exception as e2:
                print(f"  WARNING: No poster available: {e2}", file=sys.stderr)

    posted_this_cycle = 0
    escalated_today = _get_escalation_count(st)

    max_attempts = min(len(conversations), slots * 3)  # Try up to 3x slots to account for restricted replies
    for i, conv in enumerate(conversations[:max_attempts]):
        if posted_this_cycle >= slots:
            break
        author = conv.get("author", "unknown")
        tweet_id = conv.get("tweet_id", "")
        topic_group = conv.get("topic_group", "")

        print(f"\n  [{i+1}] {author} ({topic_group}) [posted {posted_this_cycle}/{slots}]")

        # Account type safety filter (politicians, state media, etc.)
        from engagement_discover import _is_blacklisted_account_type
        bl_match = _is_blacklisted_account_type(
            author, conv.get("author_bio", ""), conv.get("author_name", ""))
        if bl_match:
            print(f"    Skipping: blacklisted account type ({bl_match})")
            state_mod.mark_seen(st, tweet_id)
            continue

        # Skip authors known to have restricted replies (only for API posting)
        if poster_type == "api":
            restricted_authors = st.get("restricted_authors", [])
            if author in restricted_authors:
                print(f"    Skipping: known restricted replies (API)")
                state_mod.mark_seen(st, tweet_id)
                continue

        # Skip if recently replied to this author (but allow follow-ups — someone engaged with us)
        is_follow_up = conv.get("is_follow_up", False)
        if not is_follow_up:
            cooldown = st.get("config", {}).get("cooldown_hours", 24)
            if state_mod.recently_replied_to(st, author, cooldown):
                print(f"    Skipping: replied within {cooldown}h")
                state_mod.mark_seen(st, tweet_id)
                continue

        # Reply permissions checked at post time (draft_pipeline or poster.reply)
        # Removed per-conversation API call to avoid X API rate limits

        # Draft (skip if monitor already drafted a follow-up)
        if conv.get("draft_reply"):
            draft = conv["draft_reply"]
            print(f"    Pre-drafted ({len(draft)} chars): {draft[:80]}...")
        else:
            print(f"    Drafting...")
            try:
                draft = draft_mod.draft_reply(conv)
                print(f"    Draft ({len(draft)} chars): {draft[:80]}...")
            except Exception as e:
                print(f"    Draft failed: {e}", file=sys.stderr)
                state_mod.mark_seen(st, tweet_id)
                continue

        # Validate draft
        bad_prefixes = (
            "Error:", "The file", "I couldn't", "I can't", "I don't", "I'm sorry",
            "I cannot", "As an AI", "I apologize",
        )
        has_url = "http://" in draft or "https://" in draft
        is_bad = (
            any(draft.startswith(p) for p in bad_prefixes)
            or len(draft) < 20
            or len(draft) > 290
            or has_url
        )
        if is_bad:
            if has_url:
                reason = "contains URL"
            elif len(draft) < 20:
                reason = f"too short ({len(draft)} chars)"
            elif len(draft) > 290:
                reason = f"too long ({len(draft)} chars)"
            else:
                reason = next((p for p in bad_prefixes if draft.startswith(p)), "bad prefix")
            print(f"    Skipping bad draft: {reason}")
            state_mod.mark_seen(st, tweet_id)
            continue

        state_mod.mark_seen(st, tweet_id)
        state_mod._bump_stat(st, "drafted")

        if dry_run:
            # Classify reply type for dry-run reporting
            reply_type = _classify_reply_type(draft)
            print(f"    [DRY RUN] reply_type={reply_type}, topic={topic_group}")
            print(f"    Would {'auto-post' if autonomous else 'send to Telegram'}")
            continue

        # Evaluate (principles gate + quality evaluators)
        evaluation = None
        try:
            from draft_evaluator import evaluate_draft
            evaluation = evaluate_draft(
                draft_reply=draft,
                target_author=author,
                target_content=conv.get("content", ""),
            )
            print(f"    Evaluation: {evaluation.summary_line}")
        except Exception as e:
            print(f"    Evaluation failed ({e}), using defaults")

        # Auto-reject below minimum threshold
        if evaluation and evaluation.consensus < ESCALATION_THRESHOLD and evaluation.decision == "rejected":
            print(f"    AUTO-REJECTED (consensus {evaluation.consensus:.0%} < {ESCALATION_THRESHOLD:.0%})")
            state_mod._bump_stat(st, "eval_rejected")
            state_mod.save(st, STATE_FILE, baseline=baseline)
            continue

        # Principles gate: hard reject on principles failure
        if evaluation and evaluation.principles_score < 0.70:
            print(f"    AUTO-REJECTED (principles {evaluation.principles_score:.0%}): {evaluation.principles_feedback[:80]}")
            state_mod._bump_stat(st, "principles_rejected")
            state_mod.save(st, STATE_FILE, baseline=baseline)
            continue

        # Classify reply type for quota tracking
        reply_type = _classify_reply_type(draft)

        # Reply type diversity penalty check
        if _check_reply_type_penalty(reply_type, quotas, posted_today + posted_this_cycle):
            print(f"    Quota soft-skip: {reply_type} type over 40% today — skipping")
            # Not a hard reject — just deprioritize (skip for this cycle)
            continue

        if not autonomous:
            # Non-autonomous: send to Telegram for approval
            state_mod.save(st, STATE_FILE, baseline=baseline)
            url = conv.get("url", f"https://x.com/{author.lstrip('@')}/status/{tweet_id}")
            try:
                from draft_pipeline import process_draft_pipeline
                pipeline_result = process_draft_pipeline(
                    draft_reply=draft,
                    target_author=author,
                    target_content=conv.get("content", ""),
                    target_url=url,
                    target_tweet_id=str(tweet_id),
                    evaluate=False,  # Already evaluated
                    source="engine",
                )
                print(f"    Pipeline: {pipeline_result['action']}")
            except Exception as e:
                print(f"    Pipeline failed: {e}", file=sys.stderr)
            st, baseline = state_mod.load(STATE_FILE)
            continue

        # Autonomous mode: apply escalation tiers
        should_esc, esc_reason = _should_escalate(conv, evaluation)

        if should_esc:
            if no_escalate:
                print(f"    Auto-rejecting (no-escalate mode): {esc_reason}")
                state_mod._bump_stat(st, "eval_rejected")
                state_mod.save(st, STATE_FILE, baseline=baseline)
                continue

            if escalated_today >= escalation_max:
                print(f"    Escalation cap reached ({escalated_today}/{escalation_max}) — auto-rejecting borderline")
                state_mod._bump_stat(st, "eval_rejected")
                state_mod.save(st, STATE_FILE, baseline=baseline)
                continue

            # Escalate to Telegram via draft pipeline (registers in state, sends with buttons)
            print(f"    ESCALATING to Telegram: {esc_reason}")
            state_mod.save(st, STATE_FILE, baseline=baseline)
            url = conv.get("url", f"https://x.com/{author.lstrip('@')}/status/{tweet_id}")
            eval_info = f"consensus={evaluation.consensus:.0%}" if evaluation else "no-eval"
            try:
                from draft_pipeline import process_draft_pipeline
                esc_result = process_draft_pipeline(
                    draft_reply=draft,
                    target_author=author,
                    target_content=conv.get("content", ""),
                    target_url=url,
                    target_tweet_id=str(tweet_id),
                    evaluate=False,  # Already evaluated
                    source=f"escalated:{esc_reason}",
                )
                if esc_result['action'] in ('sent_to_telegram', 'registered_no_telegram'):
                    state_mod._bump_stat(st, "escalated")
                    escalated_today += 1
                    state_mod.save(st, STATE_FILE, baseline=baseline)
                print(f"    Escalation: {esc_result['action']} ({eval_info})")
            except Exception as e:
                print(f"    Escalation send failed: {e}", file=sys.stderr)
            st, baseline = state_mod.load(STATE_FILE)
            quotas = _get_daily_quotas(st)
            continue

        # Auto-post via Chrome
        if not poster:
            print(f"    ERROR: chrome_poster not available")
            continue

        try:
            tweet_url = conv.get("url") or f"https://x.com/i/status/{tweet_id}"
            our_tweet_id = poster(tweet_url, draft)
            print(f"    AUTO-POSTED (tweet {our_tweet_id})")

            # Record in state
            from datetime import timedelta
            st.setdefault("posted", []).append({
                "id": f"auto_{our_tweet_id}",
                "our_tweet_id": our_tweet_id,
                "target_tweet_id": str(tweet_id),
                "target_author": author,
                "draft_reply": draft,
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "analyze_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                "mode": "autonomous",
                "topic_group": topic_group,
                "reply_type": reply_type,
                "principles_score": evaluation.principles_score if evaluation else 0.7,
                "eval_consensus": evaluation.consensus if evaluation else 0.65,
                "source_query": conv.get("source_query", ""),
            })
            state_mod._bump_stat(st, "posted")
            state_mod._bump_stat(st, "auto_posted")
            _update_quotas(st, topic_group, reply_type)
            posted_this_cycle += 1

            # Notify Telegram (info only — no approval needed)
            try:
                _send_info_message(
                    f"<b>AUTO-POSTED</b> → {_escape_html(author)}\n"
                    f"<i>{_escape_html(draft[:180])}</i>\n"
                    f"<a href='https://x.com/FairDataSociety/status/{our_tweet_id}'>View reply</a>"
                )
            except Exception:
                pass

        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg and poster_type == "api":
                print(f"    Skipping (restricted replies — API can't post)")
                restricted = st.setdefault("restricted_authors", [])
                if author not in restricted:
                    restricted.append(author)
                    st["restricted_authors"] = restricted[-500:]
            elif "login" in err_msg.lower() or "auth" in err_msg.lower():
                print(f"    Chrome auth expired — stopping cycle")
                state_mod.save(st, STATE_FILE, baseline=baseline)
                break
            else:
                print(f"    Post failed: {err_msg[:100]}")

        state_mod.save(st, STATE_FILE, baseline=baseline)
        st, baseline = state_mod.load(STATE_FILE)
        quotas = _get_daily_quotas(st)

        # Rate limit between posts
        if i < slots - 1:
            time.sleep(2)

    # Final save
    st, baseline = state_mod.load(STATE_FILE)
    state_mod.save(st, STATE_FILE, baseline=baseline)

    # Summary
    posted_final = state_mod.today_count(st, "posted")
    print(f"\n  Summary:")
    print(f"    Discovered: {len(conversations)}")
    print(f"    Posted this cycle: {posted_this_cycle}")
    print(f"    Posted today total: {posted_final}/{daily_reply_limit}")
    print(f"    Escalated today: {_get_escalation_count(st)}/{escalation_max}")
    print(f"    Pending: {len(st.get('pending', []))}")

    # Write heartbeat so watchdog knows engine ran successfully
    if not dry_run:
        try:
            from engagement_watchdog import write_heartbeat
            write_heartbeat("engine")
        except Exception as e:
            print(f"  Heartbeat write failed: {e}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    autonomous = "--autonomous" in sys.argv
    no_escalate = "--no-escalate" in sys.argv
    run(dry_run=dry_run, autonomous=autonomous, no_escalate=no_escalate)
