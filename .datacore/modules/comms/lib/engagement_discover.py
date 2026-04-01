#!/usr/bin/env python3
"""Multi-source conversation discovery for engagement pipeline.

Sources (queried in order, results merged):
  1. x.ai    — Grok x_search (natural language, best relevance filtering)
  2. x_api   — X API v2 recent search (structured queries, uses X API credits)
  3. x_home  — X API v2 home timeline ("For You" tab — X does discovery for us)
  4. exa     — Exa.ai semantic search (finds X/Twitter URLs via web search)

Each source returns conversations in a common format. The orchestrator deduplicates
across sources, applies blacklists, and sorts by relevance.

New sources can be added by implementing a function with signature:
    def _discover_via_<name>(state: dict) -> List[dict]
and adding it to DISCOVERY_SOURCES.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import List, Callable


# ── Configuration ────────────────────────────────────────────────────────────

# Dedup window: don't re-draft same tweet within 7 days
DEDUP_WINDOW_DAYS = 7

# Minimum follower count for target accounts (from report: <10k = near-invisible)
MIN_FOLLOWER_COUNT = 10000
# Viral post thresholds — bypass follower filter if tweet has high engagement
VIRAL_LIKES_THRESHOLD = 50
VIRAL_RETWEETS_THRESHOLD = 20

# Account type blacklist — bio/name keywords that indicate accounts we should
# never engage with (politicians, regulators, military, religious, state media).
# Matching is case-insensitive against author bio and name.
BLACKLIST_BIO_KEYWORDS = [
    # Politicians / Government officials
    "member of parliament", "mp for", "congressman", "congresswoman",
    "senator ", "governor ", "mayor of", "minister of", "secretary of state",
    "elected representative", "city council", "state representative",
    "member of congress", "mep ", "political party", "campaign for",
    "join our party", "federal staff", "restore britain", "vote for",
    "u.s. representative", "u.s. senator",
    # Regulators
    "regulator", "regulatory body", "fcc commissioner", "sec commissioner",
    "ofcom", "data protection authority", "information commissioner",
    # Military / Defense
    "military", "armed forces", "ministry of defence", "ministry of defense",
    "pentagon", "nato ", "army ", "navy ", "air force", "marine corps",
    "defense contractor", "defence contractor", "intelligence agency",
    # Religious institutions
    "church of", "diocese", "archdiocese", "mosque", "synagogue",
    "ministry of god", "pastor at", "reverend ", "bishop of",
    "religious leader", "faith leader", "evangelical",
    # State media / propaganda
    "state media", "state-run", "government media", "official account of the",
    "press tv", "tehran times", "rt news", "sputnik", "xinhua", "cgtn",
    "global times", "people's daily", "tass news",
]

# Handle-level blacklist for known accounts to never engage with
BLACKLIST_HANDLES = {
    # State media
    "@tehrantimes79",    # Iranian state media
    "@prelogovac",       # Serbian state media personality
    "@rt_com",           # Russia Today
    "@cgaborit",         # State media
    # Politicians / government officials
    "@repaoc",           # US congresswoman
    "@senschumer",       # US senator
    "@repjimjordan",     # US congressman
    "@tedcruz",          # US senator
    "@marcorubio",       # US senator
    "@govrondesantis",   # US governor
    "@repthomasmassie",  # US congressman KY4
    "@restorebritain_",  # UK political party
    # Religious-identity accounts
    "@whitlockjason",    # Christian commentator
    # Platform owners / AI products (conflict of interest)
    "@elonmusk",         # X platform owner
    "@gaboratx",         # Grok AI
    # State-adjacent media
    "@mayadeenenglish",  # Al Mayadeen (Lebanese state-adjacent)
    # Adult content / off-brand
    "@xnxx_en",          # Porn site account
}

# Priority target accounts (from X analysis reports — high ROI per reply)
PRIORITY_TARGETS = {
    "@bigbrotherwatch", "@tutaprivacy", "@grapheneos", "@eff",
    "@protonmail", "@vitalikbuterin", "@wesroth", "@avilarenata",
    "@krassenstein", "@signal", "@torproject", "@mozilla",
    "@accessnow", "@epicprivacy", "@web3aboratory", "@logos_network",
}

# Discovery sources in priority order. Each entry: (name, function_name, required_env_var_or_None)
# Feed-first model: home timeline is primary, mentions search for responsiveness.
# x_api search and x.ai disabled by default (expensive, low marginal value over feed).
# Re-enable via config: remove from "disabled_sources" in engagement-state.json.
DISCOVERY_SOURCES = [
    ("x_home", "_discover_via_x_home", "FDS_X_API_KEY"),
    ("x_api",  "_discover_via_x_api",  "FDS_X_API_KEY"),
    ("x.ai",   "_discover_via_xai",    "XAI_API_KEY"),
    ("exa",    "_discover_via_exa",    "EXA_API_KEY"),
]


# ── Account Type Filtering ──────────────────────────────────────────────────

def _is_blacklisted_account_type(author: str, bio: str = "", name: str = "") -> str | None:
    """Check if account matches blacklisted categories by bio/name keywords.

    Returns the matched keyword if blacklisted, None otherwise.
    """
    # Handle-level blacklist (exact match)
    if author.lower() in BLACKLIST_HANDLES:
        return f"handle:{author}"

    # Bio/name keyword matching (case-insensitive)
    check_text = f"{bio} {name}".lower()
    for keyword in BLACKLIST_BIO_KEYWORDS:
        if keyword in check_text:
            return f"bio:{keyword}"

    return None


# ── Main Orchestrator ────────────────────────────────────────────────────────

def discover(state: dict, xai_api_key: str = None) -> List[dict]:
    """Find conversations to engage with from all available sources.

    Queries all configured sources, merges results, deduplicates by tweet_id,
    applies blacklists and the 7-day dedup window, and sorts by relevance.

    Args:
        state: engagement state dict (for rotation index, seen list, config)
        xai_api_key: x.ai API key override (or from env)

    Returns:
        List of conversation dicts: [{tweet_id, author, content, url, views,
        followers, relevance, topic_group, source}]
    """
    config = state.get("config", {})
    seen = state.get("seen", {})
    blacklist = set(config.get("blacklist_authors", []))
    blacklist.add("@FairDataSociety")
    now = datetime.now(timezone.utc)

    # Override env if explicit key passed
    if xai_api_key:
        os.environ["XAI_API_KEY"] = xai_api_key

    all_conversations = []
    seen_tweet_ids = set()
    source_stats = {}

    # Run all available sources
    source_functions = {
        "_discover_via_xai": _discover_via_xai,
        "_discover_via_x_api": _discover_via_x_api,
        "_discover_via_x_home": _discover_via_x_home,
        "_discover_via_exa": _discover_via_exa,
    }

    for source_name, func_name, required_env in DISCOVERY_SOURCES:
        # Check if source is available
        if required_env and not os.environ.get(required_env):
            print(f"  [{source_name}] skipped — {required_env} not set")
            source_stats[source_name] = "skipped"
            continue

        # Check if source is disabled in config
        disabled_sources = config.get("disabled_sources", [])
        if source_name in disabled_sources:
            print(f"  [{source_name}] disabled in config")
            source_stats[source_name] = "disabled"
            continue

        func = source_functions.get(func_name)
        if not func:
            continue

        try:
            print(f"  [{source_name}] querying...")
            convs = func(state)
            source_stats[source_name] = len(convs)
            print(f"  [{source_name}] found {len(convs)} conversations")
        except Exception as e:
            print(f"  [{source_name}] failed: {e}")
            source_stats[source_name] = f"error: {str(e)[:60]}"
            continue

        # Filter and dedup against other sources + seen list
        for conv in convs:
            tid = str(conv.get("tweet_id", ""))
            author = conv.get("author", "")
            reply_settings = conv.get("reply_settings", "unknown")

            if not tid or tid in seen_tweet_ids:
                continue

            # 7-day dedup window against state
            if tid in seen:
                seen_at_str = seen.get(tid, "")
                if seen_at_str:
                    try:
                        seen_at = datetime.fromisoformat(seen_at_str)
                        if (now - seen_at).days < DEDUP_WINDOW_DAYS:
                            continue
                    except Exception:
                        continue
                else:
                    continue

            if author.lower() in {b.lower() for b in blacklist}:
                continue

            # Account type blacklist (politicians, regulators, military, religious, state media)
            author_bio = conv.get("author_bio", "")
            author_name = conv.get("author_name", "")
            bl_match = _is_blacklisted_account_type(author, author_bio, author_name)
            if bl_match:
                print(f"    Filtered {author}: {bl_match}")
                continue

            # Minimum follower threshold (skip near-invisible targets)
            followers = conv.get("followers", 0) or 0
            min_followers = config.get("min_followers", MIN_FOLLOWER_COUNT)
            tweet_likes = conv.get("likes", 0) or 0
            tweet_rts = conv.get("retweets", 0) or 0
            is_viral = tweet_likes >= VIRAL_LIKES_THRESHOLD or tweet_rts >= VIRAL_RETWEETS_THRESHOLD
            if followers > 0 and followers < min_followers:
                # Exceptions: viral posts, FDS mentions, and follow-ups bypass follower filter
                if is_viral:
                    pass  # viral posts are worth replying to regardless of account size
                elif conv.get("topic_group") != "fds_mentions" and not conv.get("is_follow_up"):
                    continue

            # Skip tweets with known reply restrictions
            if reply_settings in ("mentioned_users", "mentionedUsers",
                                  "following", "subscribers", "verified"):
                continue

            # Priority target bonus (+3 relevance for high-ROI accounts)
            if author.lower() in PRIORITY_TARGETS:
                conv["relevance"] = min(10, conv.get("relevance", 7) + 3)

            # FDS mentions bonus (+2 relevance — warm targets)
            if conv.get("topic_group") == "fds_mentions":
                conv["relevance"] = min(10, conv.get("relevance", 7) + 2)

            seen_tweet_ids.add(tid)
            all_conversations.append(conv)

    # Print source summary
    print(f"  Discovery summary: {source_stats}")
    print(f"  Total unique conversations: {len(all_conversations)}")

    # Sort by relevance (highest first)
    all_conversations.sort(key=lambda c: c.get("relevance", 0), reverse=True)
    return all_conversations


# ── Source 1: x.ai (Grok x_search) ──────────────────────────────────────────

XAI_URL = "https://api.x.ai/v1/responses"
XAI_QUERIES_PER_CYCLE = 6  # Rotates through pool of 18 over 3 cycles

XAI_QUERY_POOL = [
    # ── Group 1: Privacy Architecture (4) ──────────────────────────────────────
    ("privacy_arch", "Find recent tweets (last 24h) about zero-knowledge proofs for identity or data, self-sovereign data architectures, or end-to-end encryption for files from builders and researchers. Focus on substantive technical or philosophical arguments, not product promotions. Exclude @FairDataSociety."),
    ("privacy_arch", "Find recent tweets (last 24h) about privacy by design, end-to-end encryption for files, or self-sovereign data from thoughtful accounts. Look for conversations where someone is making a genuine argument about privacy architecture, not just promoting a product. Exclude @FairDataSociety."),
    ("privacy_arch", "Find recent tweets (last 24h) about decentralized storage, censorship-resistant file sharing, peer-to-peer data transfer, or alternatives to centralized cloud storage. Focus on technical or ideological discussions, not token price. Exclude @FairDataSociety."),
    ("privacy_arch", "Find recent tweets (last 24h) from crypto/web3 builders or cypherpunk voices discussing privacy infrastructure, data sovereignty, decentralized identity, or censorship resistance. EXCLUDE token price discussions, airdrop promotions, and 'Good morning crypto' tweets. Include only substantive technical or philosophical arguments. Exclude @FairDataSociety."),

    # ── Group 2: Surveillance & Rights (4) ─────────────────────────────────────
    ("surveillance", "Find recent tweets (last 24h) about government surveillance, mass data collection, metadata tracking, or Big Tech privacy violations from people who are genuinely concerned (not promoting a product). Focus on tweets with real engagement from accounts with >1K followers. Exclude @FairDataSociety."),
    ("surveillance", "Find recent tweets (last 24h) about data breaches, GDPR enforcement, privacy policy outrage, or people frustrated with surveillance capitalism (Google tracking, Meta data selling, etc). Look for tweets expressing genuine frustration, not product promotions. Exclude @FairDataSociety."),
    ("surveillance", "Find recent tweets (last 24h) from journalists, reporters, or press freedom advocates discussing source protection, secure communication, whistleblower tools, or digital safety. Focus on tweets from verified journalists or press freedom organizations. Exclude @FairDataSociety."),
    ("surveillance", "Find recent tweets (last 24h) about facial recognition misuse, biometric data collection, location tracking, or corporate surveillance overreach from civil liberties perspectives. Look for substantive criticism, not just outrage farming. Exclude @FairDataSociety."),

    # ── Group 3: Fair Data Economy (4) ─────────────────────────────────────────
    ("fair_data", "Find recent tweets (last 24h) about data portability, right to data export, GDPR Article 20 data portability, or people frustrated by data lock-in from Big Tech platforms. Focus on substantive arguments about data rights, not policy talking points. Exclude @FairDataSociety."),
    ("fair_data", "Find recent tweets (last 24h) about data interoperability, open data standards, ActivityPub/AT Protocol federation, or the case for open protocols vs walled gardens. Look for builders and thinkers making arguments, not just advocates. Exclude @FairDataSociety."),
    ("fair_data", "Find recent tweets (last 24h) about data commons, collective data ownership, community data rights, or the idea that data generated by communities should benefit those communities. Look for thoughtful takes, not abstract policy. Exclude @FairDataSociety."),
    ("fair_data", "Find recent tweets (last 24h) about the economics of data — who should profit from data, data as labor, data dividend proposals, or critiques of Big Tech extracting value from user data without fair compensation. Exclude @FairDataSociety."),

    # ── Group 4: AI + Data Rights (3) ──────────────────────────────────────────
    ("ai_data", "Find recent tweets (last 24h) about AI training data rights — artists, writers, or creators discussing whether their work should be used without consent, opt-out mechanisms, or fair compensation for AI training. Exclude @FairDataSociety."),
    ("ai_data", "Find recent tweets (last 24h) about AI agents and data privacy — concerns about AI systems accessing personal files, MCP servers handling sensitive data, or the need for privacy-preserving AI infrastructure. Focus on builders and researchers. Exclude @FairDataSociety."),
    ("ai_data", "Find recent tweets (last 24h) about synthetic data, federated learning, or privacy-preserving machine learning as alternatives to centralized data collection. Look for technical discussions, not hype. Exclude @FairDataSociety."),

    # ── Group 5: FDS Mentions (1) ───────────────────────────────────────────────
    ("fds_mentions", "Find recent tweets (last 24h) that mention @FairDataSociety or #FairData or Fairdrop (the file sharing app). Include direct mentions, replies, quote-tweets. These are warm engagement targets — people already in our orbit. Return all results regardless of engagement level."),

    # ── Group 6: Infrastructure Alternatives (2) ────────────────────────────────
    ("infrastructure", "Find recent tweets (last 24h) from people frustrated with WeTransfer, Google Drive, Dropbox, or iCloud — complaining about privacy, file size limits, data collection, or looking for alternatives. Real user complaints, not competitor marketing. Exclude @FairDataSociety."),
    ("infrastructure", "Find recent tweets (last 24h) about self-hosted alternatives to cloud services — Nextcloud, Matrix, IPFS, Ethereum storage, or other decentralized infrastructure. Look for people actively building or advocating, not just discussing theory. Exclude @FairDataSociety."),
]

XAI_SYSTEM_PROMPT = """You are a research assistant finding conversations worth engaging with for @FairDataSociety — a project building privacy-first file sharing (Fairdrop) and data sovereignty infrastructure.

IMPORTANT FILTERING RULES:
- EXCLUDE tweets that are just promoting a crypto token or DeFi project
- EXCLUDE "Good morning/night" style engagement farming tweets
- EXCLUDE tweets that are just retweeting with no original thought
- EXCLUDE tweets from bot-like accounts or low-quality engagement farmers
- EXCLUDE tweets with restricted replies (only mentioned users, only followers, only subscribers, only verified)
- EXCLUDE tweets from politicians, government officials, regulators, military/defense accounts, religious institutions, and state media (RT, CGTN, Press TV, Tehran Times, Xinhua, etc.)
- ONLY include tweets where reply_settings is "everyone" — i.e. anyone can reply
- PREFER tweets from real people expressing genuine opinions, frustrations, or insights
- PREFER tweets with real engagement (replies, not just likes from bots)
- PREFER journalists, researchers, developers, privacy advocates, tech professionals
- PREFER accounts with genuine audiences (10K–2M followers) — more reach per reply
- STRONGLY PREFER accounts with 50K+ followers — data shows 3-4x more impressions per reply
- Do NOT avoid large accounts — high-follower authors mean higher impression counts for our reply

For each relevant tweet found, return a JSON array of objects with these fields:
- tweet_id: the tweet's ID
- author: @handle of the tweet author
- author_name: display name of the tweet author
- author_bio: short bio/description of the tweet author (if available)
- content: the full tweet text
- url: the tweet URL (https://x.com/{author}/status/{tweet_id})
- views: estimated view count (number, or 0 if unknown)
- followers: author's follower count (number, or 0 if unknown)
- relevance: 1-10 score for how relevant this is to privacy, data sovereignty, or file sharing
- reply_settings: "everyone", "mentioned_users", "following", "subscribers", or "unknown"

Return ONLY the JSON array, no other text. If no relevant tweets found, return [].
Only include tweets with relevance >= 7 AND reply_settings of "everyone" or "unknown"."""


def _discover_via_xai(state: dict) -> List[dict]:
    """Discovery via x.ai Grok x_search (natural language queries)."""
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        return []

    config = state.get("config", {})
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conversations = []
    rotation_start = config.get("query_rotation_index", 0) % len(XAI_QUERY_POOL)

    for i in range(XAI_QUERIES_PER_CYCLE):
        idx = (rotation_start + i) % len(XAI_QUERY_POOL)
        topic_group, query = XAI_QUERY_POOL[idx]

        try:
            convs = _xai_run_query(api_key, query, from_date)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate" in err_str.lower() or "credit" in err_str.lower():
                print(f"    x.ai credits exhausted at query {i}: {e}")
                break
            if "403" in err_str or "401" in err_str:
                print(f"    x.ai auth error at query {i}: {e}")
                break
            print(f"    x.ai query {idx} ({topic_group}) failed: {e}")
            continue

        for conv in convs:
            conv["topic_group"] = topic_group
            conv["source"] = "xai"
            conv["source_query"] = query
            conversations.append(conv)

    # Advance rotation index
    config["query_rotation_index"] = (rotation_start + XAI_QUERIES_PER_CYCLE) % len(XAI_QUERY_POOL)
    return conversations


def _xai_run_query(api_key: str, query: str, from_date: str) -> List[dict]:
    """Execute a single x.ai discovery query."""
    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "input": [
            {"role": "system", "content": XAI_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "tools": [{"type": "x_search", "x_search": {
            "from_date": from_date,
            "excluded_x_handles": ["FairDataSociety"],
        }}],
    }
    body = json.dumps(payload).encode()
    req = Request(XAI_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "DatacoreEngagement/1.0")

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        raise Exception(f"x.ai API error {e.code}: {error_body}")

    raw_text = ""
    for item in result.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    raw_text += block.get("text", "")
    return _parse_json_array(raw_text)


# ── Source 2: X API v2 Recent Search ─────────────────────────────────────────

X_API_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
X_API_MAX_QUERIES = 10

X_API_SEARCH_QUERIES = [
    ("privacy_arch", '("zero-knowledge" OR "self-sovereign" OR "end-to-end encryption" OR "decentralized storage") -is:retweet lang:en -"airdrop" -"giveaway" -from:FairDataSociety'),
    ("surveillance", '("surveillance" OR "data breach" OR "privacy violation" OR "mass tracking") -is:retweet lang:en -"airdrop" -"giveaway" -from:FairDataSociety'),
    ("ai_agents", '("AI agent" OR "agentic" OR "MCP server" OR "MCP tool") ("data" OR "file" OR "storage" OR "privacy") -is:retweet lang:en -"airdrop" -from:FairDataSociety'),
    ("provenance", '("data provenance" OR "AI audit trail" OR "AI accountability" OR "proof of origin" OR "data authenticity" OR "deepfake proof") -is:retweet lang:en -from:FairDataSociety'),
    ("fds_mentions", '(@FairDataSociety OR #FairData OR "Fairdrop" OR "fair data society") -is:retweet'),
    ("decentralized_ai", '("decentralized AI" OR "open source AI" OR "local AI" OR "sovereign AI") ("data" OR "privacy" OR "ownership") -is:retweet lang:en -"airdrop" -from:FairDataSociety'),
    ("fair_data", '("data sovereignty" OR "data commons" OR "data rights" OR "who owns the data" OR "fair data") -is:retweet lang:en -"airdrop" -from:FairDataSociety'),
]


def _discover_via_x_api(state: dict) -> List[dict]:
    """Discovery via X API v2 recent search."""
    poster = _get_x_poster()
    if not poster:
        return []

    # Support query override from config (feed-first: only run fds_mentions)
    config = state.get("config", {})
    query_filter = config.get("x_api_queries_override")
    queries = X_API_SEARCH_QUERIES
    if query_filter:
        queries = [(tg, q) for tg, q in queries if tg in query_filter]

    conversations = []
    for i, (topic_group, query) in enumerate(queries):
        if i >= X_API_MAX_QUERIES:
            break
        try:
            convs = _x_api_search(poster, query)
            for c in convs:
                c["topic_group"] = topic_group
                c["source"] = "x_api"
            conversations.extend(convs)
        except Exception as e:
            if "429" in str(e):
                print(f"    X API rate limited at query {i}")
                break
            print(f"    X API query {i} ({topic_group}) failed: {e}")
    return conversations


def _x_api_search(poster, query: str) -> List[dict]:
    """Execute a single X API v2 recent search query."""
    import urllib.parse as _urlparse
    params = {
        "query": query,
        "max_results": "15",
        "tweet.fields": "author_id,created_at,public_metrics,reply_settings,text",
        "user.fields": "username,public_metrics,description,name",
        "expansions": "author_id",
    }
    url = f"{X_API_SEARCH_URL}?{_urlparse.urlencode(params)}"
    result = poster._oauth_get(url)
    return _parse_x_api_tweets(result)


# ── Source 3: X API Home Timeline ("For You" tab) ────────────────────────────

X_HOME_TIMELINE_URL = "https://api.x.com/2/users/{user_id}/timelines/reverse_chronological"
X_HOME_MAX_RESULTS = 50  # Get a decent batch from the algorithmic feed

# Keywords to filter home timeline tweets for engagement relevance
HOME_RELEVANCE_KEYWORDS = [
    # Core privacy
    "privacy", "encryption", "data sovereignty", "self-sovereign", "decentralized",
    "surveillance", "data breach", "data rights", "zero-knowledge",
    "censorship", "peer-to-peer", "p2p", "cypherpunk", "digital rights",
    # AI agents + data custody (primary growth topic)
    "AI agent", "MCP", "agentic", "AI tool", "LLM", "Claude", "GPT",
    "AI data", "training data", "AI privacy", "AI audit",
    # Data provenance (new Fairdrop feature)
    "provenance", "data provenance", "audit trail", "proof of origin",
    "data authenticity", "deepfake", "fake content", "who created",
    "verifiable", "data lineage", "chain of custody",
    # Fair data / products
    "FairDataSociety", "Fairdrop", "fair data", "data commons",
    "file sharing", "cloud storage", "WeTransfer", "Dropbox", "IPFS", "Swarm",
    # Decentralized AI (dedicated target community)
    "decentralized AI", "open source AI", "local AI", "sovereign AI",
    "federated", "on-device", "edge AI",
]


def _discover_via_x_home(state: dict) -> List[dict]:
    """Discovery via X API home timeline (algorithmic "For You" feed).

    X's algorithm surfaces relevant conversations based on our account's
    interests and network — effectively free discovery with high relevance.
    """
    poster = _get_x_poster()
    if not poster:
        return []

    user_id = os.environ.get("FDS_X_USER_ID")
    if not user_id:
        print("    FDS_X_USER_ID not set, can't read home timeline")
        return []

    import urllib.parse as _urlparse
    params = {
        "max_results": str(X_HOME_MAX_RESULTS),
        "tweet.fields": "author_id,created_at,public_metrics,reply_settings,text",
        "user.fields": "username,public_metrics,description,name",
        "expansions": "author_id",
    }
    url = X_HOME_TIMELINE_URL.format(user_id=user_id) + "?" + _urlparse.urlencode(params)

    try:
        result = poster._oauth_get(url)
    except Exception as e:
        print(f"    Home timeline fetch failed: {e}")
        return []

    all_tweets = _parse_x_api_tweets(result)

    # Filter for relevance — only keep tweets matching our topic keywords
    relevant = []
    for tweet in all_tweets:
        content = tweet.get("content", "").lower()
        matched_keywords = [kw for kw in HOME_RELEVANCE_KEYWORDS if kw.lower() in content]

        if not matched_keywords:
            continue

        # Assign topic group based on keyword matches
        topic_group = _classify_home_tweet(matched_keywords)
        tweet["topic_group"] = topic_group
        tweet["source"] = "x_home"

        # Boost relevance for keyword density
        base_relevance = tweet.get("relevance", 7)
        keyword_boost = min(2, len(matched_keywords) - 1)
        tweet["relevance"] = min(10, base_relevance + keyword_boost)

        relevant.append(tweet)

    print(f"    Home timeline: {len(all_tweets)} total, {len(relevant)} relevant")
    return relevant


def _classify_home_tweet(keywords: list) -> str:
    """Classify a home timeline tweet into a topic group based on matched keywords."""
    keyword_str = " ".join(keywords).lower()
    if any(k in keyword_str for k in ("privacy", "encryption", "zero-knowledge", "self-sovereign", "decentralized", "p2p")):
        return "privacy_arch"
    if any(k in keyword_str for k in ("surveillance", "gdpr", "data breach", "censorship", "digital rights")):
        return "surveillance"
    if any(k in keyword_str for k in ("data sovereignty", "data rights", "data portability", "fair data")):
        return "fair_data"
    if any(k in keyword_str for k in ("ai agent", "mcp", "agentic")):
        return "ai_agents"
    if any(k in keyword_str for k in ("fairdatasociety", "fairdrop")):
        return "fds_mentions"
    if any(k in keyword_str for k in ("wetransfer", "dropbox", "google drive", "ipfs", "file sharing", "cloud storage")):
        return "infrastructure"
    return "privacy_arch"  # default


# ── Source 4: Exa.ai Semantic Search ─────────────────────────────────────────

EXA_API_URL = "https://api.exa.ai/search"

EXA_QUERIES = [
    ("privacy_arch", "privacy-first decentralized file sharing encryption site:x.com"),
    ("surveillance", "surveillance data breach privacy violation site:x.com"),
    ("fair_data", "data sovereignty data portability digital rights site:x.com"),
    ("ai_data", "AI agent data privacy MCP server site:x.com"),
    ("ai_agents", "AI agent file transfer infrastructure agentic site:x.com"),
    ("infrastructure", "self-hosted cloud alternative privacy site:x.com"),
]

EXA_MAX_QUERIES = 6


def _discover_via_exa(state: dict) -> List[dict]:
    """Discovery via Exa.ai semantic search for X/Twitter content.

    Exa finds tweets via semantic similarity, complementing keyword-based
    X API search with meaning-based discovery.
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return []

    conversations = []

    for i, (topic_group, query) in enumerate(EXA_QUERIES):
        if i >= EXA_MAX_QUERIES:
            break

        try:
            convs = _exa_search(api_key, query, topic_group)
            conversations.extend(convs)
        except Exception as e:
            print(f"    Exa query {i} ({topic_group}) failed: {e}")
            continue

    return conversations


def _exa_search(api_key: str, query: str, topic_group: str) -> List[dict]:
    """Execute a single Exa.ai search query filtered to x.com results."""
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "query": query,
        "numResults": 10,
        "startPublishedDate": start_date,
        "includeDomains": ["x.com", "twitter.com"],
        "type": "neural",
        "contents": {"text": {"maxCharacters": 500}},
    }

    try:
        import requests as _requests
        resp = _requests.post(
            EXA_API_URL,
            json=payload,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        raise Exception(f"Exa API error: {e}")

    conversations = []
    for item in result.get("results", []):
        url = item.get("url", "")

        # Extract tweet_id and username from URL
        # Format: https://x.com/username/status/1234567890
        tweet_id, username = _parse_x_url(url)
        if not tweet_id:
            continue

        text = item.get("text", "") or item.get("title", "")

        conversations.append({
            "tweet_id": tweet_id,
            "author": f"@{username}",
            "content": text[:280],
            "url": url,
            "views": 0,
            "followers": 0,
            "relevance": 7,  # Base — Exa already filtered by relevance
            "reply_settings": "unknown",
            "topic_group": topic_group,
            "source": "exa",
        })

    return conversations


def _parse_x_url(url: str) -> tuple:
    """Extract (tweet_id, username) from an X/Twitter URL.

    Returns ("", "") if URL doesn't match expected format.
    """
    # https://x.com/username/status/1234567890
    # https://twitter.com/username/status/1234567890
    for prefix in ("https://x.com/", "https://twitter.com/",
                    "http://x.com/", "http://twitter.com/"):
        if url.startswith(prefix):
            path = url[len(prefix):].rstrip("/").split("?")[0]
            parts = path.split("/")
            if len(parts) >= 3 and parts[1] == "status":
                return parts[2], parts[0]
    return "", ""


# ── Shared Helpers ───────────────────────────────────────────────────────────

def _get_x_poster():
    """Get an XPoster instance for X API calls. Returns None on failure."""
    try:
        from x_poster import XPoster
        return XPoster(account='fds', user_id=os.environ.get('FDS_X_USER_ID'))
    except Exception as e:
        print(f"    Can't init XPoster: {e}")
        return None


def _parse_x_api_tweets(result: dict) -> List[dict]:
    """Parse X API v2 response into conversation dicts."""
    tweets = result.get("data", []) or []
    users = {u["id"]: u for u in (result.get("includes", {}).get("users", []) or [])}

    conversations = []
    for tweet in tweets:
        author_id = tweet.get("author_id", "")
        user = users.get(author_id, {})
        username = user.get("username", "unknown")
        followers = user.get("public_metrics", {}).get("followers_count", 0)
        metrics = tweet.get("public_metrics", {})
        reply_settings = tweet.get("reply_settings", "everyone")

        # Skip restricted replies
        if reply_settings in ("mentionedUsers", "following", "subscribers"):
            continue

        views = metrics.get("impression_count", 0)
        likes = metrics.get("like_count", 0)
        replies = metrics.get("reply_count", 0)

        relevance = 7
        if followers > 50000:
            relevance += 2
        elif followers > 10000:
            relevance += 1
        if likes > 10 or replies > 3:
            relevance += 1
        relevance = min(10, relevance)

        conversations.append({
            "tweet_id": tweet["id"],
            "author": f"@{username}",
            "author_name": user.get("name", ""),
            "author_bio": user.get("description", ""),
            "content": tweet.get("text", ""),
            "url": f"https://x.com/{username}/status/{tweet['id']}",
            "views": views,
            "followers": followers,
            "likes": likes,
            "retweets": metrics.get("retweet_count", 0),
            "relevance": relevance,
            "reply_settings": "everyone" if reply_settings == "everyone" else reply_settings,
        })

    return conversations


def _parse_json_array(text: str) -> List[dict]:
    """Extract JSON array from LLM response text."""
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return []
