#!/usr/bin/env python3
"""Weekly X engagement deep analysis for @FairDataSociety.

Fetches last 7 days of tweet data, enriches with target author profiles,
recent followers, and retweeters, then generates a full HTML report via Gemini.

Usage:
    python3 x_weekly_analysis.py                    # Last 7 days, open report
    python3 x_weekly_analysis.py --days 14          # Custom window
    python3 x_weekly_analysis.py --no-open          # Don't auto-open

Output:
    ~/Data/2-projectspace/2-projects/fds-x-deepanalysis-YYYY-MM-DD.html
"""

import json
import os
import sys
import time
import hmac
import hashlib
import base64
import urllib.parse
import uuid
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(Path.home() / "Data/.datacore/env/.env")

CK  = os.environ["X_CONSUMER_KEY"]
CS  = os.environ["X_CONSUMER_SECRET"]
AT  = os.environ["X_ACCESS_TOKEN"]
ATS = os.environ["X_ACCESS_TOKEN_SECRET"]
USER_ID = "1100728268729262081"  # @FairDataSociety

import google.generativeai as genai
genai.configure(api_key=os.environ["GEMINI_API_KEY"])


# ── OAuth ──────────────────────────────────────────────────────────────────────

def percent_encode(s):
    return urllib.parse.quote(str(s), safe='')

def oauth_header(method, url, extra_params=None):
    params = {"oauth_consumer_key": CK, "oauth_nonce": uuid.uuid4().hex,
              "oauth_signature_method": "HMAC-SHA1",
              "oauth_timestamp": str(int(time.time())),
              "oauth_token": AT, "oauth_version": "1.0"}
    if extra_params:
        params.update(extra_params)
    sp = "&".join(f"{percent_encode(k)}={percent_encode(v)}"
                  for k, v in sorted(params.items()))
    base = "&".join([method.upper(), percent_encode(url), percent_encode(sp)])
    sk = f"{percent_encode(CS)}&{percent_encode(ATS)}"
    sig = base64.b64encode(
        hmac.new(sk.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(params.items()) if k.startswith("oauth_")
    )

def api_get(url, qp=None, retries=1):
    qp = qp or {}
    full_url = url + ("?" + urllib.parse.urlencode(qp) if qp else "")
    r = requests.get(full_url, headers={"Authorization": oauth_header("GET", url, qp)})
    if r.status_code == 429 and retries > 0:
        print("  Rate limited, waiting 15s...")
        time.sleep(15)
        return api_get(url, qp, retries - 1)
    return r.json()


# ── Data Collection ────────────────────────────────────────────────────────────

def fetch_tweets(days: int) -> tuple[dict, list]:
    """Returns (account_metrics, tweets_list). Paginates to get all tweets."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    account = api_get("https://api.twitter.com/2/users/me",
                      {"user.fields": "public_metrics,description,created_at"})["data"]

    # Paginate to get all tweets
    all_tweets = []
    includes_users = {}
    pagination_token = None
    for page in range(10):  # safety limit
        qp = {
            "max_results": "100", "start_time": start,
            "tweet.fields": "created_at,public_metrics,referenced_tweets,text",
            "expansions": "referenced_tweets.id,referenced_tweets.id.author_id",
            "user.fields": "public_metrics,description,name,username",
            "exclude": "retweets",
        }
        if pagination_token:
            qp["pagination_token"] = pagination_token
        resp = api_get(f"https://api.twitter.com/2/users/{USER_ID}/tweets", qp)
        tweets = resp.get("data", [])
        all_tweets.extend(tweets)
        for u in resp.get("includes", {}).get("users", []):
            includes_users[u["id"]] = u
        pagination_token = resp.get("meta", {}).get("next_token")
        print(f"  Page {page+1}: {len(tweets)} tweets (total: {len(all_tweets)})")
        if not pagination_token:
            break
        time.sleep(2)  # rate limit courtesy

    # Enrich each tweet
    enriched = []
    for t in all_tweets:
        dt = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
        m = t["public_metrics"]
        refs = t.get("referenced_tweets", [])
        types = [r["type"] for r in refs]
        kind = "reply" if "replied_to" in types else ("quote" if "quoted" in types else "original")

        # Find target author from includes
        target_author = None
        for r in refs:
            # The author of the referenced tweet would be in includes_users
            # We match by looking at the text for @mentions as fallback
            pass

        score = (m["like_count"] * 3 + m["reply_count"] * 5 +
                 m["retweet_count"] * 4 + m["impression_count"] // 100)

        enriched.append({
            "id": t["id"],
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "hour": dt.hour,
            "kind": kind,
            "text": t["text"],
            "impressions": m["impression_count"],
            "likes": m["like_count"],
            "retweets": m["retweet_count"],
            "replies_received": m["reply_count"],
            "bookmarks": m["bookmark_count"],
            "engagement_score": score,
            "url": f"https://x.com/FairDataSociety/status/{t['id']}",
        })

    return account, sorted(enriched, key=lambda x: x["date"] + x["time"])


def fetch_target_authors(tweets: list) -> list:
    """Fetch profiles of accounts we replied to (from tweet text @mentions)."""
    import re
    target_handles = []
    for t in tweets:
        if t["kind"] in ("reply", "quote"):
            # Extract first @mention from reply text
            m = re.match(r'^@(\w+)', t["text"])
            if m:
                target_handles.append(m.group(1).lower())

    # Deduplicate, keep order
    seen = set()
    unique = [h for h in target_handles if not (h in seen or seen.add(h))]

    if not unique:
        return []

    # Batch lookup (max 100)
    batch = unique[:100]
    resp = api_get("https://api.twitter.com/2/users/by", {
        "usernames": ",".join(batch),
        "user.fields": "public_metrics,description,name,username",
    })
    return resp.get("data", [])


def fetch_followers(max_results: int = 100) -> list:
    """Fetch recent followers."""
    resp = api_get(f"https://api.twitter.com/2/users/{USER_ID}/followers", {
        "max_results": str(max_results),
        "user.fields": "public_metrics,description,name,username,created_at",
    })
    return resp.get("data", [])


def fetch_retweeters(tweets: list) -> list:
    """Fetch retweeters for tweets that got retweeted."""
    retweeters = []
    for t in tweets:
        if t["retweets"] > 0:
            resp = api_get(f"https://api.twitter.com/2/tweets/{t['id']}/retweeted_by",
                           {"user.fields": "public_metrics,description,name,username"})
            for u in resp.get("data", []):
                u["retweeted_tweet"] = t["text"][:80]
                retweeters.append(u)
            time.sleep(1)
    return retweeters


# ── Analysis ───────────────────────────────────────────────────────────────────

def compute_daily_stats(tweets: list) -> dict:
    daily = defaultdict(lambda: {"posts": 0, "orig": 0, "rep": 0, "qt": 0,
                                  "imp": 0, "likes": 0, "rt": 0, "score": 0})
    for t in tweets:
        d = daily[t["date"]]
        d["posts"] += 1
        d[{"reply": "rep", "quote": "qt", "original": "orig"}[t["kind"]]] += 1
        d["imp"] += t["impressions"]
        d["likes"] += t["likes"]
        d["rt"] += t["retweets"]
        d["score"] += t["engagement_score"]
    return dict(daily)


def compute_timing_stats(tweets: list) -> dict:
    hourly = defaultdict(lambda: {"count": 0, "imp": 0})
    for t in tweets:
        h = hourly[t["hour"]]
        h["count"] += 1
        h["imp"] += t["impressions"]
    return {
        hour: {**v, "avg_imp": v["imp"] // v["count"] if v["count"] else 0}
        for hour, v in hourly.items()
    }


def cluster_content_topics(tweets: list) -> dict:
    """Simple keyword clustering of tweet content."""
    clusters = {
        "Age verification / Digital ID": ["age verif", "digital id", "age gate", "id check", "age ban"],
        "ZK proofs / Privacy tech": ["zero-knowledge", "zk proof", "zk age", "cryptograph", "fairdrop", "swarm"],
        "Surveillance / Data brokers": ["surveillance", "broker", "profile", "tracking", "location data"],
        "GDPR / Regulation": ["gdpr", "data act", "regulation", "ico", "dpc", "sovereignty"],
        "Encryption / Architecture": ["encrypt", "architecture", "metadata", "protocol", "self-host"],
        "NHS / Health data": ["nhs", "health data", "medical", "patient"],
        "Social media / Platforms": ["twitter", "x's", "meta", "facebook", "whatsapp", "grok"],
    }
    results = defaultdict(lambda: {"count": 0, "imp": 0, "likes": 0, "tweets": []})
    for t in tweets:
        text_lower = t["text"].lower()
        matched = False
        for cluster, keywords in clusters.items():
            if any(kw in text_lower for kw in keywords):
                results[cluster]["count"] += 1
                results[cluster]["imp"] += t["impressions"]
                results[cluster]["likes"] += t["likes"]
                results[cluster]["tweets"].append(t["text"][:80])
                matched = True
                break
        if not matched:
            results["Other"]["count"] += 1
            results["Other"]["imp"] += t["impressions"]
    return dict(results)


# ── Report Generation ──────────────────────────────────────────────────────────

def generate_report(account, tweets, target_authors, followers, retweeters,
                    daily, timing, topics, days, output_path):
    """Generate full HTML report via Gemini."""

    total_posts = len(tweets)
    total_imp = sum(t["impressions"] for t in tweets)
    total_likes = sum(t["likes"] for t in tweets)
    total_rt = sum(t["retweets"] for t in tweets)

    top5 = sorted(tweets, key=lambda x: -x["impressions"])[:5]
    bottom10 = sorted(tweets, key=lambda x: x["impressions"])[:10]

    # Build tweet table rows (all tweets for HTML, top 30 for prompt)
    def _tweet_row(t):
        kind_badge = {"reply": "🔵", "quote": "🟡", "original": "🟢"}[t["kind"]]
        text = t["text"][:100].replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        return f"""<tr onclick="window.open('{t['url']}','_blank')" style="cursor:pointer">
      <td>{t['date']} {t['time']}</td>
      <td>{kind_badge} {t['kind']}</td>
      <td class="text-cell" title="{text}">{text}{'…' if len(t['text'])>100 else ''}</td>
      <td class="num">{t['impressions']:,}</td>
      <td class="num">{t['likes']}</td>
      <td class="num">{t['retweets']}</td>
      <td class="num">{t['replies_received']}</td>
      <td class="num score">{t['engagement_score']}</td>
    </tr>"""
    sorted_tweets = sorted(tweets, key=lambda x: -x["impressions"])
    tweet_rows = "".join(_tweet_row(t) for t in sorted_tweets)
    # Only send top 30 to Gemini to avoid prompt size limits
    prompt_tweet_rows = "".join(_tweet_row(t) for t in sorted_tweets[:30])

    days_sorted = sorted(daily.keys())
    chart_data = {
        "labels": days_sorted,
        "posts": [daily[d]["posts"] for d in days_sorted],
        "impressions": [daily[d]["imp"] for d in days_sorted],
        "likes": [daily[d]["likes"] for d in days_sorted],
        "scores": [daily[d]["score"] for d in days_sorted],
        "orig": [daily[d]["orig"] for d in days_sorted],
        "replies": [daily[d]["rep"] for d in days_sorted],
        "quotes": [daily[d]["qt"] for d in days_sorted],
    }

    timing_labels = sorted(timing.keys())
    timing_chart = {
        "hours": [f"{h:02d}:00" for h in timing_labels],
        "avg_imp": [timing[h]["avg_imp"] for h in timing_labels],
        "count": [timing[h]["count"] for h in timing_labels],
    }

    um = account["public_metrics"]
    period_start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%b %-d")
    period_end = datetime.now(timezone.utc).strftime("%b %-d, %Y")

    top_authors_text = "\n".join(
        f"- @{u['username']} ({u['public_metrics']['followers_count']:,} followers): {u.get('description','')[:80]}"
        for u in sorted(target_authors, key=lambda x: -x["public_metrics"]["followers_count"])[:15]
    ) if target_authors else "No author data"

    notable_followers = sorted(followers, key=lambda x: -x["public_metrics"]["followers_count"])[:10]
    followers_text = "\n".join(
        f"- @{u['username']} ({u['public_metrics']['followers_count']:,} followers): {u.get('description','')[:80]}"
        for u in notable_followers
    ) if notable_followers else "No follower data"

    topics_text = "\n".join(
        f"- {topic}: {v['count']} tweets, {v['imp']:,} total impressions, {v['likes']} likes"
        for topic, v in sorted(topics.items(), key=lambda x: -x[1]["imp"])
        if topic != "Other"
    )

    top5_text = "\n".join(
        f"{i+1}. {t['impressions']:,} impr | {t['likes']} likes | {t['kind']} | {t['text'][:120]}"
        for i, t in enumerate(top5)
    )

    prompt = f"""Create a professional dark-theme HTML analytics report for @FairDataSociety's X engagement.

PERIOD: {period_start} – {period_end} ({days} days)
ACCOUNT: followers={um['followers_count']}, following={um['following_count']}, total_tweets={um['tweet_count']}
TOTAL POSTS: {total_posts} (originals={sum(1 for t in tweets if t['kind']=='original')}, replies={sum(1 for t in tweets if t['kind']=='reply')}, quotes={sum(1 for t in tweets if t['kind']=='quote')})
TOTAL IMPRESSIONS: {total_imp:,}
TOTAL LIKES: {total_likes}
TOTAL RETWEETS: {total_rt}
AVG IMPRESSIONS/POST: {total_imp//total_posts if total_posts else 0}

CHART DATA (embed directly in JS):
{json.dumps(chart_data)}

TIMING CHART DATA:
{json.dumps(timing_chart)}

TOP 5 POSTS:
{top5_text}

CONTENT TOPICS (by impressions):
{topics_text}

TARGET ACCOUNTS WE REPLIED TO (top 15 by followers):
{top_authors_text}

NOTABLE NEW FOLLOWERS:
{followers_text}

RETWEETERS: {len(retweeters)} unique retweeters

TWEET TABLE (top 30 pre-built HTML rows — use these as-is in the table):
{prompt_tweet_rows}
NOTE: The full tweet table ({total_posts} rows) will be injected after generation. Only include the table structure with thead — the tbody content shown above is a sample.

Design: dark theme — bg #0d1117, cards #161b22, accent #58a6ff, green #3fb950, gold #d29922, red #f85149
Use Chart.js from CDN.
IMPORTANT RENDERING RULES:
1. In JavaScript for Chart.js, use ACTUAL HEX COLOR VALUES (e.g. '#58a6ff'), NOT CSS var() references. Chart.js cannot resolve CSS custom properties. CSS variables are fine in <style> but must NOT appear in <script>.
2. Chart containers must have a fixed height (e.g. height: 350px; position: relative) — do NOT let charts expand unconstrained.
3. The full tweet table must be in a scrollable container (max-height: 600px; overflow-y: auto).
4. Keep the page lightweight — avoid heavy animations or transitions that cause scroll lag.

Include these sections:
1. Header with period + generated timestamp
2. 5 KPI stat cards (impressions, likes, retweets, posts, avg impr/post)
3. Charts row: daily impressions bar + post type stacked bar + hourly avg impressions bar
4. Content topic performance table (topic, tweets, total impr, avg impr, likes)
5. Target audience section — table of accounts we engaged with (followers, description)
6. Notable new followers table
7. Evaluator verdicts — 4 cards:
   - Steve Jobs: ruthless simplicity, what to cut, the ONE thing
   - Jeff Bezos: customer obsession, working backwards, long-term compounding
   - First Principles: strip assumptions, what would you do from zero with this data
   - Pattern Synthesizer: top 3 highest-leverage changes backed by empirical evidence
8. Strategic recommendations — top 5 ranked by expected impact, specific and actionable
9. Full sortable tweet table (all {total_posts} tweets, click row → opens tweet)

Make evaluator cards visually distinct — each with its own accent color and a bold one-line verdict headline.
All tables sortable by clicking headers. Each tweet row clickable → opens tweet URL in new tab.
Embed Chart.js from CDN. System-ui font. No external dependencies except Chart.js.

Output ONLY the complete HTML. No preamble."""

    print("Generating report with Gemini...")
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt, request_options={"timeout": 300})

    html = response.text
    if html.startswith("```"):
        html = "\n".join(html.split("\n")[1:])
        if html.rstrip().endswith("```"):
            html = "\n".join(html.rstrip().split("\n")[:-1])

    # Inject full tweet table (Gemini only got top 30 to stay within prompt limits)
    # Replace the tbody content with all tweet rows
    import re
    tbody_pattern = r'(<tbody>\s*)(.*?)(</tbody>)'
    # Find the LAST tbody (the full tweet table is always last)
    tbodies = list(re.finditer(tbody_pattern, html, re.DOTALL))
    if tbodies:
        last = tbodies[-1]
        html = html[:last.start(2)] + "\n" + tweet_rows + "\n                " + html[last.end(2):]
        print(f"  Injected full tweet table ({total_posts} rows replacing top-30 sample)")

    output_path.write_text(html)
    print(f"Report saved: {output_path} ({len(html):,} chars)")
    return output_path


# ── Main ───────────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    """Send a message via Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("ENGAGEMENT_CHAT_ID")
    if not token or not cid:
        print("No Telegram credentials, skipping notification")
        return
    payload = json.dumps({
        "chat_id": cid, "text": message,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }).encode()
    from urllib.request import Request as _Req, urlopen as _urlopen
    req = _Req(f"https://api.telegram.org/bot{token}/sendMessage",
               data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with _urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        if not result.get("ok"):
            print(f"Telegram error: {result}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="FDS X weekly deep analysis")
    parser.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    parser.add_argument("--no-open", action="store_true", help="Don't open report in browser")
    parser.add_argument("--telegram", action="store_true", help="Send summary to Telegram")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = Path.home() / f"Data/3-fds/1-tracks/comms/reports/fds-x-deepanalysis-{today}.html"

    print(f"Fetching {args.days} days of data for @FairDataSociety...")
    account, tweets = fetch_tweets(args.days)
    print(f"  {len(tweets)} tweets fetched")

    print("Fetching target author profiles...")
    target_authors = fetch_target_authors(tweets)
    print(f"  {len(target_authors)} author profiles")

    print("Fetching recent followers...")
    followers = fetch_followers()
    print(f"  {len(followers)} followers")

    print("Fetching retweeters...")
    retweeters = fetch_retweeters(tweets)
    print(f"  {len(retweeters)} retweeters")

    daily = compute_daily_stats(tweets)
    timing = compute_timing_stats(tweets)
    topics = cluster_content_topics(tweets)

    report_path = generate_report(
        account, tweets, target_authors, followers, retweeters,
        daily, timing, topics, args.days, out
    )

    if not args.no_open:
        import subprocess
        subprocess.Popen(["open", str(report_path)])

    if args.telegram:
        um = account["public_metrics"]
        total_imp = sum(t["impressions"] for t in tweets)
        total_likes = sum(t["likes"] for t in tweets)
        top = sorted(tweets, key=lambda x: -x["impressions"])[:3]
        top_lines = "\n".join(
            f"  {i+1}. {t['impressions']:,} impr | {t['likes']} ❤️  {t['text'][:70]}…"
            for i, t in enumerate(top)
        )
        period_start = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%b %-d")
        period_end = datetime.now(timezone.utc).strftime("%b %-d")
        msg = (
            f"<b>📊 Weekly X Analysis — {period_start}–{period_end}</b>\n\n"
            f"Followers: <b>{um['followers_count']:,}</b>\n"
            f"Posts: <b>{len(tweets)}</b>  |  Impressions: <b>{total_imp:,}</b>  |  Likes: <b>{total_likes}</b>\n"
            f"Avg impr/post: <b>{total_imp//len(tweets) if tweets else 0}</b>\n\n"
            f"<b>Top posts:</b>\n{top_lines}\n\n"
            f"<i>Full report: fds-x-deepanalysis-{today}.html (sync to get it)</i>"
        )
        send_telegram(msg)
        print("Telegram summary sent")

    print(f"\nDone: {report_path}")


if __name__ == "__main__":
    main()
