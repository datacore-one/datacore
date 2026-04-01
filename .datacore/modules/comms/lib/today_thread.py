#!/usr/bin/env python3
"""Today in Privacy — daily thread curation and posting.

Curates 5-7 tweet thread from:
  1. newsfeed-top.jsonl — top-scored timeline tweets (last 24h, score ≥ 4)
  2. Nightshift research outputs with privacy tag
  3. High-score discovery items (score 7+, not yet drafted as replies)

Approval flow:
  1. Draft thread → send to Telegram at 05:30 UTC
  2. Buttons: Post Now / Edit / Skip Today
  3. Auto-post at 09:00 UTC if no response

Usage:
    python3 today_thread.py             # Draft + send to Telegram
    python3 today_thread.py --dry-run   # Print draft without sending
    python3 today_thread.py --post {id} # Post an already-drafted thread

Posting: tweet 1 via x_poster.post(), tweets 2+ via x_poster.reply(in_reply_to=last_id)
Each tweet separated by 5 seconds.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
NEWSFEED_FILE = DATA_DIR / ".datacore" / "state" / "newsfeed-top.jsonl"
THREAD_STATE_KEY = "threads"

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(DATA_DIR / ".datacore" / "lib"))

MIN_ITEMS = 3          # Skip thread if fewer than this many sources available
MIN_THREAD_TWEETS = 6  # Minimum thread length (intro + items)
MAX_THREAD_TWEETS = 15 # Maximum thread length — use when there's enough quality material
SKIP_IF_FEWER = 3      # From settings, min source items required
MAX_NEWS_AGE_HOURS = 36  # Only use recent news (engram: previous day's news only)


def load_env():
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── Source Gathering ──────────────────────────────────────────────────────────

def _get_newsfeed_items(max_items: int = 5) -> list:
    """Read top-scored items from newsfeed-top.jsonl (last 24h)."""
    if not NEWSFEED_FILE.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    items = []
    seen_domains = set()

    for line in NEWSFEED_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            captured = datetime.fromisoformat(entry.get("captured_at", ""))
            if captured < cutoff:
                continue
            url = entry.get("url", "")
            domain = _extract_domain(url)

            # Max 1 item per domain
            if domain and domain in seen_domains:
                continue
            if domain:
                seen_domains.add(domain)

            items.append({
                "type": "newsfeed",
                "tweet_id": entry.get("tweet_id", ""),
                "content": entry.get("content", ""),
                "url": url,
                "score": entry.get("score", 0),
            })
        except Exception:
            continue

    # Sort by score descending, take top items
    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    return items[:max_items]


def _get_research_items(max_items: int = 3) -> list:
    """Read privacy-tagged nightshift research outputs."""
    items = []
    inbox_dir = DATA_DIR / "0-personal" / "0-inbox"
    if not inbox_dir.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)

    for f in sorted(inbox_dir.glob("nightshift-*.md"), reverse=True)[:10]:
        try:
            stat = f.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

            content = f.read_text()
            # Look for privacy/data-related content
            keywords = ["privacy", "data sovereignty", "surveillance", "encryption",
                        "GDPR", "data rights", "data portability", "fair data"]
            content_lower = content.lower()
            if not any(kw.lower() in content_lower for kw in keywords):
                continue

            # Extract first URL and title from the file
            url = _extract_first_url(content)
            title = _extract_title(content, f.name)

            if not url:
                continue

            items.append({
                "type": "research",
                "content": title,
                "url": url,
                "score": 5,  # Research items get baseline score
                "source_file": str(f.name),
            })

            if len(items) >= max_items:
                break
        except Exception:
            continue

    return items


def _get_discovery_items(st: dict, max_items: int = 3) -> list:
    """Get high-score (7+) discovery items not yet drafted as replies."""
    # These would come from the seen dict — we don't have full data stored
    # For now, this is a future enhancement placeholder
    # High-score items are candidates in the current cycle, not stored between runs
    return []


def _get_solutions_items(max_items: int = 2) -> list:
    """Search for positive privacy news: products, tools, wins.

    Uses Exa API to find recent launches and developments showing structural
    privacy working — open protocols gaining adoption, ZK tools shipping,
    portability wins, self-hosted alternatives growing.

    Falls back gracefully to empty list if Exa unavailable.
    """
    import requests as _requests

    api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        return []

    queries = [
        "privacy preserving tool launch open source decentralized 2026",
        "data portability interoperability open protocol win 2026",
        "zero knowledge proof encryption self-hosted privacy product 2026",
        "federated protocol ActivityPub fediverse adoption milestone 2026",
        "data sovereignty personal data store open standard 2026",
    ]

    candidates = []
    seen_urls = set()
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "accept": "application/json",
    }

    for query in queries:
        try:
            payload = {
                "query": query,
                "numResults": 3,
                "type": "auto",
                "contents": {"text": {"maxCharacters": 400}},
                "startPublishedDate": (
                    datetime.now(timezone.utc) - timedelta(hours=MAX_NEWS_AGE_HOURS)
                ).strftime("%Y-%m-%dT00:00:00Z"),
            }
            resp = _requests.post(
                "https://api.exa.ai/search",
                json=payload, headers=headers, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for result in data.get("results", []):
                url = result.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = result.get("title", "")
                snippet = (result.get("text") or "")[:400].strip()
                if not title and not snippet:
                    continue

                # Filter out negative/threat news — solutions only
                combined = (title + " " + snippet).lower()
                negative_signals = [
                    "breach", "leak", "hack", "fined", "lawsuit", "violat",
                    "ban", "spy", "surveillance", "exploit", "attack",
                ]
                positive_signals = [
                    "launch", "release", "open source", "portab", "interop",
                    "encrypt", "decentrali", "self-host", "zero-knowledge",
                    "sovereign", "open protocol", "privacy-preserv", "federat",
                ]
                neg_count = sum(1 for s in negative_signals if s in combined)
                pos_count = sum(1 for s in positive_signals if s in combined)

                if pos_count == 0 or neg_count > pos_count:
                    continue

                candidates.append({
                    "type": "solution",
                    "title": title,
                    "snippet": snippet,
                    "content": title,  # kept for backward compat
                    "url": url,
                    "score": 5 + pos_count,
                })

        except Exception:
            continue

    # Sort by score, deduplicate domains
    candidates.sort(key=lambda x: x["score"], reverse=True)
    seen_domains = set()
    results = []
    for c in candidates:
        domain = _extract_domain(c["url"])
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        results.append(c)
        if len(results) >= max_items:
            break

    return results


def _get_category_search_items(max_items: int = 8) -> list:
    """Run multi-category privacy news search via Exa.

    Per engram ENG-2026-0310-009: build full inventory across
    breach/surveillance/policy/regulation before drafting.
    """
    import requests as _requests

    api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        return []

    # Specific, targeted queries — not generic keyword dumps
    categories = {
        "breach": [
            "major data breach million records exposed",
            "healthcare data breach patient records stolen",
            "company hack credentials leaked database",
        ],
        "surveillance": [
            "government surveillance expansion new law",
            "facial recognition deployment police ICE",
            "mass monitoring tracking citizens",
        ],
        "policy": [
            "privacy regulation passed GDPR enforcement fine",
            "data protection law new legislation",
            "age verification online safety bill",
        ],
        "crypto-privacy": [
            "zero knowledge proof privacy blockchain launch",
            "encrypted messaging protocol update",
            "decentralized identity self-sovereign",
        ],
    }

    candidates = []
    seen_urls = set()
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "accept": "application/json",
    }

    for category, queries in categories.items():
        for query in queries:
            try:
                payload = {
                    "query": query,
                    "numResults": 3,
                    "type": "auto",
                    "contents": {"text": {"maxCharacters": 400}},
                    "startPublishedDate": (
                        datetime.now(timezone.utc) - timedelta(hours=MAX_NEWS_AGE_HOURS)
                    ).strftime("%Y-%m-%dT00:00:00Z"),
                }
                resp = _requests.post(
                    "https://api.exa.ai/search",
                    json=payload, headers=headers, timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                for result in data.get("results", []):
                    url = result.get("url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    title = result.get("title", "")
                    snippet = (result.get("text") or "")[:400].strip()
                    if not title and not snippet:
                        continue

                    candidates.append({
                        "type": category,
                        "title": title,
                        "snippet": snippet,
                        "content": title,
                        "url": url,
                        "score": 5,
                    })

            except Exception:
                continue

    # Deduplicate domains
    seen_domains = set()
    results = []
    for c in candidates:
        domain = _extract_domain(c["url"])
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        results.append(c)
        if len(results) >= max_items:
            break

    return results


def _get_grok_trending_items(max_items: int = 5) -> list:
    """Search X/Twitter via Grok (xAI) for trending privacy stories.

    Grok has real-time X data — finds breaking stories, original tweets
    to reference/tag, and what's actually being discussed right now.
    """
    import requests as _requests

    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    prompt = (
        "Search X/Twitter for the most significant privacy, data breach, "
        "surveillance, and data rights stories from the last 24 hours. "
        "For each story, provide:\n"
        "1. A one-sentence summary\n"
        "2. The source article URL (not the tweet URL)\n"
        "3. The original tweet URL if a notable account broke/discussed it\n"
        "4. Any relevant accounts to tag (@handle)\n\n"
        "Return as JSON array: [{\"summary\": \"...\", \"url\": \"...\", "
        "\"tweet_url\": \"...\", \"tags\": [\"@handle\", ...]}]\n"
        "Only include stories with verifiable source URLs. Max 8 stories."
    )

    try:
        payload = {
            "model": "grok-3-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "search": {"mode": "on"},
        }
        resp = _requests.post(
            "https://api.x.ai/v1/chat/completions",
            json=payload, headers=headers, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        # Parse JSON from response
        items = _parse_grok_results(content)
        return items[:max_items]

    except Exception as e:
        print(f"  Grok search failed: {e}")
        return []


def _parse_grok_results(content: str) -> list:
    """Parse Grok search results into source items."""
    import re

    # Try to extract JSON array
    start = content.find("[")
    end = content.rfind("]")
    if start < 0 or end <= start:
        return []

    try:
        results = json.loads(content[start:end+1])
    except Exception:
        return []

    items = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url", "")
        if not url:
            continue
        summary = r.get("summary", "")
        tags = r.get("tags", [])
        tweet_url = r.get("tweet_url", "")

        items.append({
            "type": "grok-trending",
            "title": summary,
            "content": summary,
            "url": url,
            "tweet_url": tweet_url,
            "tags": tags,
            "score": 7,  # Trending items get higher score
        })

    return items


def _verify_url(url: str) -> bool:
    """Quick check that URL is reachable (HEAD request, 5s timeout)."""
    if not url or not url.startswith("http"):
        return False
    try:
        from urllib.request import Request as _Req, urlopen as _urlopen
        req = _Req(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0")
        with _urlopen(req, timeout=5) as resp:
            return resp.status < 400
    except Exception:
        # Try GET as fallback (some servers reject HEAD)
        try:
            from urllib.request import Request as _Req2, urlopen as _urlopen2
            req = _Req2(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with _urlopen2(req, timeout=5) as resp:
                return resp.status < 400
        except Exception:
            return False


def _inject_tags(tweets: list, sources: list) -> list:
    """Post-process tweets to inject @tags from source data.

    LLMs frequently drop @mentions despite prompt instructions.
    This ensures tags from Grok-discovered sources appear in tweets.

    Rules:
    - Skip tweet 1 (intro) — no tags on the opening tweet
    - Each @handle appears at most once across the entire thread
    - Only inject into tweets that mention the brand by name
    """
    # Build a map: source URL -> tags
    source_tags = []
    for src in sources:
        tags = src.get("tags", [])
        if not tags:
            continue
        url = src.get("url", "")
        title = (src.get("title", "") or src.get("content", "")).lower()
        source_tags.append({"url": url, "title": title, "tags": tags})

    used_tags = set()  # Track which @handles have been used
    result = []

    for idx, tweet in enumerate(tweets):
        # Skip intro tweet — no tags
        if idx == 0:
            result.append(tweet)
            continue

        tweet_lower = tweet.lower()
        for src in source_tags:
            for tag in src["tags"]:
                if not tag.startswith("@"):
                    continue
                tag_lower = tag.lower()
                # Skip if already used in thread
                if tag_lower in used_tags:
                    continue
                # Skip if already present in tweet
                if tag_lower in tweet_lower:
                    used_tags.add(tag_lower)
                    continue

                brand = tag[1:].lower()
                import re
                pattern = re.compile(re.escape(brand), re.IGNORECASE)
                if pattern.search(tweet):
                    tweet = pattern.sub(tag, tweet, count=1)
                    tweet_lower = tweet.lower()
                    used_tags.add(tag_lower)

        result.append(tweet)
    return result


def _generate_statement_card(intro_tweet: str) -> 'Path | None':
    """Generate a statement card image for the first tweet.

    Extracts a punchy 1-2 line statement from the intro tweet.
    Returns path to generated image, or None on failure.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  PIL not available — skipping statement card")
        return None

    # Extract the hook from the intro tweet (after the date line)
    lines = intro_tweet.split("\n")
    # Skip "1/ Today in Privacy — ..." line, take the meat
    statement_lines = [l.strip() for l in lines[1:] if l.strip()]
    if not statement_lines:
        return None

    # Take first sentence, cap at ~60 chars for visual impact
    statement = statement_lines[0]
    if len(statement) > 80:
        # Try to split at a natural break
        for sep in [". ", " — ", ": ", ", "]:
            idx = statement.find(sep)
            if 20 < idx < 70:
                statement = statement[:idx + (1 if sep == ". " else 0)]
                break
        else:
            statement = statement[:75] + "..."

    # Split into 2 lines if long
    if len(statement) > 40:
        mid = len(statement) // 2
        # Find nearest space to midpoint
        space_before = statement.rfind(" ", 0, mid + 10)
        if space_before > mid - 15:
            statement = statement[:space_before] + "\n" + statement[space_before+1:]

    card_path = DATA_DIR / ".datacore" / "state" / "today-privacy-card.png"

    try:
        img = Image.new('RGB', (1200, 675), color=(10, 10, 20))
        draw = ImageDraw.Draw(img)

        font_large = None
        font_small = None
        for font_path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]:
            if Path(font_path).exists():
                try:
                    font_large = ImageFont.truetype(font_path, 72)
                    font_small = ImageFont.truetype(font_path, 28)
                    break
                except Exception:
                    pass

        if font_large is None:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Draw accent line
        draw.rectangle([80, 80, 120, 595], fill=(0, 180, 255))

        # Draw statement text
        text_lines = statement.split('\n')
        y = 200
        for line in text_lines:
            draw.text((160, y), line, font=font_large, fill=(255, 255, 255))
            y += 100

        # FDS branding
        draw.text((160, 580), "@FairDataSociety · Today in Privacy",
                  font=font_small, fill=(100, 140, 180))

        img.save(str(card_path), 'PNG')
        print(f"  Statement card generated: {card_path}")
        return card_path

    except Exception as e:
        print(f"  Statement card failed: {e}")
        return None


def _upload_media(image_path: 'Path') -> 'str | None':
    """Upload image to X v1.1 media API, return media_id."""
    import base64
    import hashlib
    import hmac
    import secrets
    import urllib.parse

    MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"

    api_key = os.environ.get("FDS_X_API_KEY", "")
    api_secret = os.environ.get("FDS_X_API_SECRET", "")
    access_token = os.environ.get("FDS_X_ACCESS_TOKEN", "")
    access_secret = os.environ.get("FDS_X_ACCESS_TOKEN_SECRET", "")

    if not all([api_key, api_secret, access_token, access_secret]):
        print("  Media upload: missing X API credentials")
        return None

    try:
        image_data = image_path.read_bytes()
        b64 = base64.b64encode(image_data).decode()

        nonce = secrets.token_hex(16)
        ts = str(int(time.time()))

        def pct(s):
            return urllib.parse.quote(str(s), safe='')

        oauth_params = {
            'oauth_consumer_key': api_key,
            'oauth_nonce': nonce,
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp': ts,
            'oauth_token': access_token,
            'oauth_version': '1.0',
        }

        base_params = '&'.join(
            f"{pct(k)}={pct(v)}" for k, v in sorted(oauth_params.items())
        )
        base_string = f"POST&{pct(MEDIA_UPLOAD_URL)}&{pct(base_params)}"
        signing_key = f"{pct(api_secret)}&{pct(access_secret)}"
        sig = base64.b64encode(
            hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()

        oauth_params['oauth_signature'] = sig
        auth_header = 'OAuth ' + ', '.join(
            f'{pct(k)}="{pct(v)}"' for k, v in sorted(oauth_params.items())
        )

        boundary = '----FormBoundary' + secrets.token_hex(8)
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="media_data"\r\n\r\n'
            f'{b64}\r\n'
            f'--{boundary}--\r\n'
        ).encode()

        from urllib.request import Request as _Req, urlopen as _urlopen
        req = _Req(MEDIA_UPLOAD_URL, data=body, method='POST')
        req.add_header('Authorization', auth_header)
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

        with _urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())

        media_id = result['media_id_string']
        print(f"  Media uploaded: {media_id}")
        return media_id

    except Exception as e:
        print(f"  Media upload failed: {e}")
        return None


def _extract_domain(url: str) -> str:
    """Extract domain from URL for dedup."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return ""


def _extract_first_url(content: str) -> str:
    """Extract first URL from markdown content."""
    import re
    # Look for markdown links first
    m = re.search(r'\[.*?\]\((https?://[^\)]+)\)', content)
    if m:
        return m.group(1)
    # Then plain URLs
    m = re.search(r'https?://\S+', content)
    return m.group(0).rstrip('.,)') if m else ""


def _extract_title(content: str, filename: str) -> str:
    """Extract title from markdown content."""
    import re
    m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return filename.replace("nightshift-", "").replace(".md", "").replace("-", " ").title()


# ─── Thread Drafting ───────────────────────────────────────────────────────────

def _draft_thread(items: list) -> list:
    """Draft a quality-driven dynamic thread from curated items.

    Thread length is determined by how much genuinely important material
    exists — between MIN_THREAD_TWEETS and MAX_THREAD_TWEETS.

    Returns: list of tweet strings
    """
    today = datetime.now(timezone.utc).strftime("%A, %B %-d")

    # Separate threats/news from solutions for structured ordering
    threat_items = [it for it in items if it.get("type") != "solution"]
    solution_items = [it for it in items if it.get("type") == "solution"]

    def fmt_item(i, item):
        tag = ""
        if item.get("type") == "solution":
            tag = " [SOLUTION]"
        elif item.get("type") == "grok-trending":
            tag = " [TRENDING]"
        title = item.get("title", "") or item.get("content", "")
        snippet = item.get("snippet", "") or item.get("content", "")
        # Show title + snippet if we have both; otherwise just content
        if title and snippet and title not in snippet:
            body = f"{title}\n   Summary: {snippet[:400]}"
        else:
            body = (title or snippet)[:400]
        extra = ""
        if item.get("tags"):
            extra += f"\n   Tags: {' '.join(item['tags'])}"
        if item.get("tweet_url"):
            extra += f"\n   Original tweet: {item['tweet_url']}"
        return f"{i+1}.{tag} {body}\n   URL: {item.get('url', '')}{extra}"

    items_text = "\n".join(fmt_item(i, item) for i, item in enumerate(items))

    solution_instruction = ""
    if solution_items:
        solution_instruction = """
- [SOLUTION] items show structural privacy working in practice — open protocols shipping, portability wins, ZK tools.
  Place these AFTER news/critique items. Frame as "here's what it looks like when it works" — not hype, not PR.
  Same format: one sentence on what it is + URL + one sentence on why it's structurally significant."""

    prompt = f"""Draft a "Today in Privacy" tweet thread for @FairDataSociety.

You are producing the definitive daily privacy thread — the one analysts, journalists, and technologists read to understand what's actually happening. Quality over brevity. Include everything that genuinely matters today. Skip anything that doesn't add a distinct angle.

Each source below includes the title and a content excerpt — that is sufficient to draft the thread. Do not attempt to fetch or visit any URLs.

Date: {today}
Sources ({len(items)} items to evaluate):
{items_text}

Thread length: between {MIN_THREAD_TWEETS} and {MAX_THREAD_TWEETS} tweets.
- Use more tweets when there are multiple distinct, important stories — don't compress or skip them.
- Use fewer tweets when sources overlap or lack genuine insight — don't pad with weak items.
- The right length is whatever covers the important material without filler.

Thread format:
1/ Today in Privacy — {today}
[2-3 sentence thematic intro. What's the through-line connecting today's stories? Be specific — name the pattern or tension.]

2/ [news item] → [url]
[1-2 sentences: what happened + why it matters structurally for data rights or privacy architecture]

... [all items that clear the quality bar] ...

[solution items last — what structural privacy looks like when it works]

Rules:
- Tweet 1: no URL, under 250 chars, must earn attention with a specific insight not a vague teaser
- CRITICAL: Every item tweet MUST include the FULL source URL from the sources list — copy it exactly, do not truncate or abbreviate
- Each item tweet: ONE sentence summary + FULL URL on its own line + ONE or TWO sentences on architectural/rights significance
- Each tweet ≤ 270 chars total (URL counts toward limit — shorten the text, never the URL)
- No emojis. No hashtags. No marketing speak. No "🧵" indicators.
- Sound like a sharp analyst who has read everything and distilled it — not a brand account
- The architectural question: why does this matter at the infrastructure level, not just as policy?
- Skip items where you can't add a genuinely useful framing — better a shorter thread than a padded one
- Only use stories from the past 24 hours — week-old news reads as stale on X
- When a source has Tags (e.g. @StarknetFndn), include the tag naturally in the tweet — tag the account, don't just mention the name
- When a source has an Original tweet URL, you may reference it (e.g. "as @StarknetFndn announced") to add social proof{solution_instruction}

Return ONLY a JSON array of tweet strings. No preamble, no explanation.
Example format: ["1/ Today in Privacy...", "2/ ...", "3/ ..."]"""

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))

    import subprocess
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "sonnet", "--output-format", "text",
             "--no-session-persistence", "--max-turns", "8"],
            input=prompt, capture_output=True, text=True,
            cwd=str(DATA_DIR), env=env, timeout=90,
        )
        if result.returncode != 0:
            raise Exception(f"Claude failed: {result.stderr[:200]}")

        output = result.stdout.strip()
        return _parse_tweets(output)

    except Exception as e:
        print(f"Draft failed: {e}")
        # Fallback: minimal thread
        return [
            f"1/ Today in Privacy — {today}\nPrivacy isn't a policy question. It's an architecture question. Today's stories:",
        ] + [
            f"{i+2}/ {item.get('content', '')[:200]}\n{item.get('url', '')}"
            for i, item in enumerate(items[:4])
        ]


def _parse_tweets(output: str) -> list:
    """Parse JSON array of tweets from LLM output."""
    output = output.strip()

    # Try direct JSON
    try:
        result = json.loads(output)
        if isinstance(result, list) and all(isinstance(t, str) for t in result):
            return result
    except Exception:
        pass

    # Try to find JSON array
    start = output.find("[")
    end = output.rfind("]")
    if start >= 0 and end > start:
        try:
            result = json.loads(output[start:end+1])
            if isinstance(result, list):
                return [str(t) for t in result]
        except Exception:
            pass

    # Fallback: split on numbered lines
    import re
    tweets = re.split(r'\n(?=\d+/)', output.strip())
    return [t.strip() for t in tweets if t.strip()]


# ─── State Management ──────────────────────────────────────────────────────────

def _save_thread(st: dict, thread_id: str, tweets: list, sources: list,
                 status: str, telegram_msg_id: int = None) -> dict:
    """Save thread to engagement state."""
    thread = {
        "id": thread_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tweets": tweets,
        "sources": sources,
        "status": status,
        "telegram_message_id": telegram_msg_id,
        "posted_tweet_ids": [],
        "auto_post_at": _auto_post_time(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    threads = st.setdefault(THREAD_STATE_KEY, [])
    # Replace if same date exists
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    threads[:] = [t for t in threads if t.get("date") != today]
    threads.append(thread)
    return thread


def _auto_post_time() -> str:
    """Return today's auto-post deadline (08:00 UTC = 09:00 CET)."""
    now = datetime.now(timezone.utc)
    deadline = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if deadline < now:
        deadline += timedelta(days=1)
    return deadline.isoformat()


def _get_today_thread(st: dict) -> dict | None:
    """Get today's thread from state."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for t in st.get(THREAD_STATE_KEY, []):
        if t.get("date") == today:
            return t
    return None


# ─── Telegram Integration ──────────────────────────────────────────────────────

def _send_thread_to_telegram(thread_id: str, tweets: list, dry_run: bool = False) -> int:
    """Send thread draft to Telegram for approval. Returns message_id."""
    # Tweets already contain "1/", "2/" prefixes — don't double them
    preview = "\n\n".join(_escape_html(t) for t in tweets)
    text = (
        f"<b>📰 Today in Privacy draft</b>\n\n"
        f"{preview}\n\n"
        f"<i>{len(tweets)} tweets · auto-posts at 08:00 UTC (09:00 CET)</i>"
    )

    keyboard = {"inline_keyboard": [[
        {"text": "✓ Post Now", "callback_data": f"thread:approve:{thread_id}"},
        {"text": "✎ Edit", "callback_data": f"thread:edit:{thread_id}"},
        {"text": "✗ Skip Today", "callback_data": f"thread:skip:{thread_id}"},
    ]]}

    if dry_run:
        print("=== THREAD DRAFT (DRY RUN) ===")
        for i, t in enumerate(tweets):
            print(f"\n--- Tweet {i+1} ---\n{t}")
        print(f"\nKeyboard: {keyboard['inline_keyboard']}")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        raise ValueError("TELEGRAM_BOT_TOKEN and ENGAGEMENT_CHAT_ID required")

    payload = {
        "chat_id": cid, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True, "reply_markup": keyboard,
    }

    body = json.dumps(payload).encode()
    from urllib.request import Request as _Req, urlopen as _urlopen
    req = _Req(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with _urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        if result.get("ok"):
            return result["result"]["message_id"]
        raise Exception(f"Telegram error: {result}")


def post_thread(thread_id: str, st: dict, state_file: Path = STATE_FILE):
    """Post a thread to X, resuming if interrupted.

    Uses posted_tweet_ids to resume from where it left off.
    """
    import engagement_state as state_mod

    thread = None
    for t in st.get(THREAD_STATE_KEY, []):
        if t.get("id") == thread_id:
            thread = t
            break

    if not thread:
        print(f"Thread {thread_id} not found in state")
        return

    tweets = thread.get("tweets", [])
    posted_ids = thread.get("posted_tweet_ids", [])
    already_posted = len(posted_ids)

    if already_posted >= len(tweets):
        print(f"Thread {thread_id} already fully posted")
        thread["status"] = "posted"
        state_mod.save(st, state_file)
        return

    from x_poster import XPoster
    poster = XPoster(account='fds', user_id=os.environ.get('FDS_X_USER_ID'))

    thread["status"] = "posting"
    state_mod.save(st, state_file)
    st, _ = state_mod.load(state_file)
    thread = _get_today_thread(st) or thread

    # Generate statement card for first tweet
    media_id = None
    if already_posted == 0:
        card_path = _generate_statement_card(tweets[0])
        if card_path:
            media_id = _upload_media(card_path)

    last_id = posted_ids[-1] if posted_ids else None

    for i, tweet_text in enumerate(tweets):
        if i < already_posted:
            continue  # Already posted, skip

        try:
            if last_id is None and media_id:
                # First tweet with statement card
                payload = {'text': tweet_text, 'media': {'media_ids': [media_id]}}
                result = poster._oauth_post("https://api.x.com/2/tweets", payload)
            elif last_id is None:
                result = poster.post(tweet_text)
            else:
                result = poster.reply(tweet_text, last_id)

            new_id = result.get('data', {}).get('id', '')
            if not new_id:
                print(f"  Tweet {i+1}: no ID returned")
                break

            last_id = new_id
            thread["posted_tweet_ids"].append(new_id)
            print(f"  Tweet {i+1}/{len(tweets)} posted: {new_id}")

            # Save after each tweet (resilient posting)
            state_mod.save(st, state_file)
            st, _ = state_mod.load(state_file)
            thread = _get_today_thread(st) or thread

            if i < len(tweets) - 1:
                time.sleep(5)  # 5s gap between tweets

        except Exception as e:
            print(f"  Tweet {i+1} failed: {e}")
            thread["status"] = "failed"
            state_mod.save(st, state_file)
            return

    thread["status"] = "posted"
    thread["posted_at"] = datetime.now(timezone.utc).isoformat()
    state_mod.save(st, state_file)
    print(f"\nThread posted: {len(thread['posted_tweet_ids'])} tweets")
    print(f"  https://x.com/FairDataSociety/status/{thread['posted_tweet_ids'][0]}")


# ─── Auto-post Check ──────────────────────────────────────────────────────────

def check_auto_post(st: dict, state_file: Path = STATE_FILE):
    """Check if a pending thread has passed its auto-post deadline."""
    thread = _get_today_thread(st)
    if not thread:
        return

    if thread.get("status") not in ("pending", "approved"):
        return

    auto_post_at = thread.get("auto_post_at", "")
    try:
        deadline = datetime.fromisoformat(auto_post_at)
    except Exception:
        return

    if datetime.now(timezone.utc) >= deadline:
        print(f"Auto-post deadline reached for thread {thread['id']}")
        import engagement_state as state_mod
        post_thread(thread["id"], st, state_file)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, force: bool = False):
    """Main: curate sources, draft thread, send to Telegram."""
    load_env()

    import engagement_state as state_mod
    st, _ = state_mod.load(STATE_FILE)

    # Check kill switch
    kill_switch = DATA_DIR / ".datacore" / "state" / "campaign-kill-switch"
    if kill_switch.exists():
        print(f"Kill switch active. Skipping thread.")
        return

    # Don't re-draft if already done today (unless forced)
    existing = _get_today_thread(st)
    if existing and not force:
        status = existing.get("status", "?")
        print(f"Today's thread already {status}. Use --force to re-draft.")
        # Still check auto-post
        check_auto_post(st, STATE_FILE)
        return

    # Gather sources — multi-category + Grok search per engram ENG-2026-0310-009
    print("Gathering sources (multi-source inventory)...")
    newsfeed = _get_newsfeed_items(max_items=10)
    research = _get_research_items(max_items=4)
    grok = _get_grok_trending_items(max_items=6)
    category = _get_category_search_items(max_items=8)
    solutions = _get_solutions_items(max_items=4)

    all_sources = grok + newsfeed + research + category + solutions
    print(f"  Grok trending: {len(grok)}")
    print(f"  Newsfeed items: {len(newsfeed)}")
    print(f"  Research items: {len(research)}")
    print(f"  Category search items: {len(category)}")
    print(f"  Solutions items: {len(solutions)}")
    print(f"  Total before URL check: {len(all_sources)}")

    # Verify URLs — per engram ENG-2026-0310-008 (every story needs working link)
    verified = []
    for item in all_sources:
        url = item.get("url", "")
        if not url:
            continue
        if _verify_url(url):
            verified.append(item)
        else:
            print(f"  DROPPED (bad URL): {url[:80]}")
    all_sources = verified
    print(f"  Total after URL check: {len(all_sources)}")

    if len(all_sources) < SKIP_IF_FEWER:
        print(f"Not enough sources ({len(all_sources)} < {SKIP_IF_FEWER}). Skipping today.")
        return

    # Pass all sources to the LLM — it selects and orders by quality
    curated = all_sources[:MAX_THREAD_TWEETS - 1]

    # Draft thread
    print("Drafting thread...")
    tweets = _draft_thread(curated)
    print(f"  Drafted {len(tweets)} tweets")
    for i, t in enumerate(tweets):
        print(f"  {i+1}: {t[:80]}...")

    if not tweets:
        print("Draft produced no tweets. Skipping.")
        return

    # Post-draft: inject @tags from source data (LLMs drop them)
    tweets = _inject_tags(tweets, curated)

    # Post-draft validation: check URLs in tweets aren't truncated
    import re
    url_pattern = re.compile(r'https?://\S+')
    for i, t in enumerate(tweets):
        if i == 0:
            continue  # Intro tweet has no URL
        urls_in_tweet = url_pattern.findall(t)
        if not urls_in_tweet:
            print(f"  WARNING: Tweet {i+1} has no URL — engram requires every story tweet includes source link")
        for url in urls_in_tweet:
            url_clean = url.rstrip('.,)')
            if url_clean.endswith('.') or len(url_clean) < 20:
                print(f"  WARNING: Tweet {i+1} has possibly truncated URL: {url_clean}")

    # Save to state (include full source data for tag injection on post)
    thread_id = f"thread_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    source_refs = [{"type": s["type"], "tweet_id": s.get("tweet_id", ""),
                    "url": s.get("url", ""), "tags": s.get("tags", []),
                    "tweet_url": s.get("tweet_url", "")} for s in curated]

    thread = _save_thread(st, thread_id, tweets, source_refs, "pending")
    state_mod.save(st, STATE_FILE)

    # Send to Telegram
    try:
        msg_id = _send_thread_to_telegram(thread_id, tweets, dry_run=dry_run)
        if not dry_run:
            st, _ = state_mod.load(STATE_FILE)
            today_thread = _get_today_thread(st)
            if today_thread:
                today_thread["telegram_message_id"] = msg_id
            state_mod.save(st, STATE_FILE)
            print(f"Thread sent to Telegram (msg {msg_id})")
    except Exception as e:
        print(f"Telegram send failed: {e}")

    return thread_id


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if "--post" in sys.argv:
        idx = sys.argv.index("--post")
        thread_id = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        if thread_id:
            load_env()
            import engagement_state as state_mod
            st, _ = state_mod.load(STATE_FILE)
            post_thread(thread_id, st, STATE_FILE)
        else:
            print("Usage: today_thread.py --post <thread_id>")
    elif "--check-auto" in sys.argv:
        load_env()
        import engagement_state as state_mod
        st, _ = state_mod.load(STATE_FILE)
        check_auto_post(st, STATE_FILE)
    else:
        run(dry_run=dry_run, force=force)
