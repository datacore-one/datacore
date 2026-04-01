#!/usr/bin/env python3
"""Monitor replies to our posted tweets and queue follow-up conversations.

Uses X API v2 mentions timeline to find replies to @FairDataSociety.
Drafts follow-up replies via Claude CLI and sends to Telegram for approval.

Run as part of the engagement engine cycle (Phase 1b).
"""

import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List


DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

# X API v2 mentions endpoint
X_MENTIONS_URL = "https://api.x.com/2/users/{user_id}/mentions"

FOLLOW_UP_PROMPT = """You are drafting a follow-up reply for @FairDataSociety in an ongoing conversation.

DO NOT read any files. All context is below. Just write the reply.

## Context

Our original tweet: {our_content}

{their_author} replied: {their_content}

## Rules

- This is a BACK-AND-FORTH, not a broadcast
- Under 240 chars
- If they agree: add one new layer or extend the thought
- If they push back: engage honestly, don't be defensive
- If they ask a question: answer directly, briefly
- Never just say "great point" or "thanks for engaging"
- No hashtags, no emojis, no URLs
- Sound like a thoughtful human, not a brand account

## Reply

Write ONLY the reply text, nothing else."""


def _get_x_poster():
    """Get XPoster for API calls."""
    try:
        from x_poster import XPoster
        return XPoster(account='fds', user_id=os.environ.get('FDS_X_USER_ID'))
    except Exception as e:
        print(f"    Monitor: XPoster unavailable: {e}")
        return None


def find_replies_to_our_tweets(posted_items: list) -> List[dict]:
    """Find replies to our recent tweets using X API v2 mentions timeline.

    Falls back to x.ai if X API credits are depleted (402).

    Args:
        posted_items: List of posted items from state (with our_tweet_id)

    Returns:
        List of reply dicts with their_tweet_id, their_author, their_content,
        our_tweet_id, our_content, url
    """
    # Build lookup: our_tweet_id → posted item
    our_tweets = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    for p in posted_items:
        our_id = p.get("our_tweet_id", "")
        if not our_id or our_id in ("unknown", "pending_chrome", ""):
            continue
        try:
            posted_at = datetime.fromisoformat(p["posted_at"])
            if posted_at < cutoff:
                continue
        except Exception:
            continue
        our_tweets[our_id] = p

    if not our_tweets:
        print("    Monitor: no recent posted tweets to check")
        return []

    # Try X API v2 mentions first
    replies = _find_replies_x_api(our_tweets)
    if replies is not None:
        return replies

    # Fallback: x.ai search
    print("    Monitor: falling back to x.ai")
    return _find_replies_xai(our_tweets)


def _find_replies_x_api(our_tweets: dict) -> list | None:
    """Find replies via X API v2 mentions. Returns None on auth/credit failure."""
    poster = _get_x_poster()
    if not poster:
        return None

    user_id = os.environ.get("FDS_X_USER_ID")
    if not user_id:
        return None

    import urllib.parse as _urlparse
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "max_results": "100",
        "start_time": since,
        "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id,referenced_tweets,text",
        "user.fields": "username",
        "expansions": "author_id,referenced_tweets.id",
    }
    url = X_MENTIONS_URL.format(user_id=user_id) + "?" + _urlparse.urlencode(params)

    try:
        result = poster._oauth_get(url)
    except Exception as e:
        err = str(e)
        if "402" in err or "429" in err or "CreditsDepleted" in err:
            print(f"    Monitor: X API credits depleted, will try x.ai fallback")
            return None  # Signal to try fallback
        print(f"    Monitor: mentions fetch failed: {e}")
        return []

    mentions = result.get("data", []) or []
    users = {u["id"]: u for u in (result.get("includes", {}).get("users", []) or [])}
    user_id_str = user_id

    print(f"    Monitor: {len(mentions)} mentions in last 48h, checking against {len(our_tweets)} posted tweets")

    replies = []
    for mention in mentions:
        refs = mention.get("referenced_tweets", [])
        replied_to_id = None
        for ref in refs:
            if ref.get("type") == "replied_to":
                replied_to_id = ref.get("id")
                break

        if not replied_to_id or replied_to_id not in our_tweets:
            continue

        author_id = mention.get("author_id", "")
        if author_id == user_id_str:
            continue

        user = users.get(author_id, {})
        username = user.get("username", "unknown")
        our_item = our_tweets[replied_to_id]

        replies.append({
            "their_tweet_id": mention["id"],
            "their_author": f"@{username}",
            "their_content": mention.get("text", ""),
            "our_tweet_id": replied_to_id,
            "our_content": our_item.get("draft_reply", ""),
            "url": f"https://x.com/{username}/status/{mention['id']}",
        })

    return replies


def _find_replies_xai(our_tweets: dict) -> list:
    """Find replies via x.ai Grok x_search (fallback when X API credits depleted)."""
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        print("    Monitor: XAI_API_KEY not set, no fallback available")
        return []

    # Build query with our recent tweet URLs
    our_urls = [
        f"https://x.com/FairDataSociety/status/{tid}"
        for tid in list(our_tweets.keys())[:10]
    ]

    query = (
        f"Find tweets from the last 48 hours that are direct replies to @FairDataSociety. "
        f"Focus on these specific tweet URLs: {', '.join(our_urls[:5])}. "
        f"Return only genuine replies where someone is engaging with the argument."
    )

    system_prompt = """Find replies to the specified @FairDataSociety tweets.

For each reply found, return a JSON array with:
- their_tweet_id: ID of their reply tweet
- their_author: @handle of the person who replied
- their_content: full text of their reply
- our_tweet_id: ID of the @FairDataSociety tweet they replied to
- url: URL of their reply tweet

Return ONLY the JSON array. If no genuine replies found, return []."""

    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "tools": [{"type": "x_search", "x_search": {
            "from_date": (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }}],
    }

    body = json.dumps(payload).encode()
    req = Request("https://api.x.ai/v1/responses", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "DatacoreMonitor/1.0")

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        print(f"    Monitor x.ai: API error {e.code}")
        return []
    except Exception as e:
        print(f"    Monitor x.ai: {e}")
        return []

    raw_text = ""
    for item in result.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    raw_text += block.get("text", "")

    # Parse JSON array
    import re
    try:
        parsed = json.loads(raw_text.strip())
        if not isinstance(parsed, list):
            parsed = []
    except json.JSONDecodeError:
        start = raw_text.find("[")
        end = raw_text.rfind("]")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw_text[start:end + 1])
            except json.JSONDecodeError:
                parsed = []
        else:
            parsed = []

    # Match against our_tweets and add our_content
    replies = []
    for r in parsed:
        our_id = str(r.get("our_tweet_id", ""))
        if our_id in our_tweets:
            r["our_content"] = our_tweets[our_id].get("draft_reply", "")
        replies.append(r)

    print(f"    Monitor x.ai: found {len(replies)} replies")
    return replies


def draft_follow_up(reply_item: dict) -> str:
    """Draft a follow-up reply using Claude CLI.

    Args:
        reply_item: {their_content, their_author, our_content}

    Returns:
        Draft reply text
    """
    prompt = FOLLOW_UP_PROMPT.format(
        our_content=reply_item.get("our_content", "")[:300],
        their_author=reply_item.get("their_author", ""),
        their_content=reply_item.get("their_content", "")[:300],
    )

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--model", "sonnet",
                "--output-format", "text",
                "--no-session-persistence",
                "--max-turns", "1",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(DATA_DIR),
            env=env,
            timeout=30,
        )
        if result.returncode == 0:
            draft = result.stdout.strip()
            # Clean up — remove quotes if wrapped
            if draft.startswith('"') and draft.endswith('"'):
                draft = draft[1:-1]
            return draft
        raise Exception(f"claude exit {result.returncode}: {result.stderr[:100]}")
    except subprocess.TimeoutExpired:
        raise Exception("Draft timed out")


def monitor(state: dict) -> List[dict]:
    """Check for replies to our tweets and return new conversations to queue.

    Returns list of conversation items ready for the engine's draft+evaluate loop.
    Follow-ups get prioritized over new discovery targets.
    """
    posted = state.get("posted", [])
    seen = state.get("seen", {})

    replies = find_replies_to_our_tweets(posted)

    new_conversations = []
    for reply in replies:
        their_id = str(reply.get("their_tweet_id", ""))

        # Skip if we've already seen/processed this reply
        if their_id in seen:
            continue

        # Check if we already have a pending draft for this tweet
        import engagement_state as state_mod
        if state_mod.has_pending_for_tweet(state, their_id):
            continue

        # Draft a follow-up
        try:
            follow_up = draft_follow_up(reply)
            print(f"    Follow-up draft for {reply['their_author']}: {follow_up[:60]}...")
        except Exception as e:
            print(f"    Follow-up draft failed for {reply.get('their_author')}: {e}")
            continue

        new_conversations.append({
            "tweet_id": their_id,
            "author": reply.get("their_author", ""),
            "content": reply.get("their_content", ""),
            "url": reply.get("url", ""),
            "views": 0,
            "followers": 0,
            "relevance": 9,  # High priority — someone is engaging with us
            "reply_settings": "everyone",
            "topic_group": "fds_mentions",
            "source": "monitor",
            "is_follow_up": True,
            "our_tweet_id": reply.get("our_tweet_id", ""),
            "our_content": reply.get("our_content", ""),
            "draft_reply": follow_up,
        })

    return new_conversations
