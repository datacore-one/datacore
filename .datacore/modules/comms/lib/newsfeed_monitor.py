# .datacore/modules/comms/lib/newsfeed_monitor.py
"""Monitor home timeline for engagement opportunities.

With a large curated follow list, the home timeline becomes a
high-quality newsfeed. This monitor:
1. Scores tweets by topic relevance
2. Auto-likes relevant content (community building)
3. Drafts quote-RTs for exceptional content (amplification)
4. Feeds engagement targets to the reply engine

Schedule: runs every 30 minutes on Nightshift.
**Like/quote-RT counters persist to SQLite** — survives systemd oneshot restarts.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Callable


# Topic keyword weights — higher = more relevant to our mission
DEFAULT_TOPIC_KEYWORDS = {
    'privacy': 3, 'encryption': 3, 'surveillance': 2,
    'decentralized': 2, 'file sharing': 3, 'data sovereignty': 3,
    'zero knowledge': 2, 'end-to-end': 2, 'metadata': 2,
    'self-hosted': 2, 'open source': 1, 'censorship': 2,
    'GDPR': 2, 'data breach': 2, 'whistleblower': 2,
    'MCP server': 2, 'AI agent': 1, 'swarm': 2,
    'peer-to-peer': 2, 'p2p': 2, 'web3': 1,
}


class NewsfeedMonitor:
    def __init__(
        self,
        poster,
        follow_db,
        like_threshold: int = 2,
        daily_like_budget: int = 100,
        quote_rt_drafter: Optional[Callable] = None,
        quote_rt_threshold: int = 5,
        daily_quote_rt_budget: int = 3,
        state_dir: str = None,
    ):
        self.poster = poster
        self.follow_db = follow_db
        self.topic_keywords = DEFAULT_TOPIC_KEYWORDS
        self.like_threshold = like_threshold
        self.daily_like_budget = daily_like_budget
        self.quote_rt_drafter = quote_rt_drafter
        self.quote_rt_threshold = quote_rt_threshold
        self.daily_quote_rt_budget = daily_quote_rt_budget

        # Persistent daily counters (shares rate-limits.db)
        sp = state_dir or str(
            Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
            / ".datacore" / "state"
        )
        Path(sp).mkdir(parents=True, exist_ok=True)
        self._rate_db = sqlite3.connect(str(Path(sp) / "rate-limits.db"))
        self._rate_db.execute("PRAGMA journal_mode=WAL")
        self._rate_db.execute("""
            CREATE TABLE IF NOT EXISTS newsfeed_counts (
                date TEXT, counter TEXT, count INTEGER DEFAULT 0,
                PRIMARY KEY (date, counter)
            )
        """)
        self._rate_db.commit()

    def _get_count(self, counter: str) -> int:
        today = time.strftime('%Y-%m-%d')
        row = self._rate_db.execute(
            "SELECT count FROM newsfeed_counts WHERE date = ? AND counter = ?",
            (today, counter),
        ).fetchone()
        return row[0] if row else 0

    def _increment_count(self, counter: str):
        today = time.strftime('%Y-%m-%d')
        self._rate_db.execute(
            """INSERT INTO newsfeed_counts (date, counter, count)
               VALUES (?, ?, 1)
               ON CONFLICT(date, counter) DO UPDATE SET count = count + 1""",
            (today, counter),
        )
        self._rate_db.commit()

    def _score_tweet(self, tweet: dict) -> int:
        """Score a tweet by topic keyword matches."""
        text_lower = tweet.get('text', '').lower()
        score = 0
        for keyword, weight in self.topic_keywords.items():
            if keyword.lower() in text_lower:
                score += weight
        return score

    def _process_tweet(self, tweet: dict, score: int):
        """Process a scored tweet: like if above threshold."""
        if score >= self.like_threshold and self._get_count('likes') < self.daily_like_budget:
            try:
                self.poster.like(tweet['id'])
                self._increment_count('likes')
                # Track engagement in follow DB
                author_id = tweet.get('author_id')
                if author_id:
                    self.follow_db.record_engagement(author_id)
            except Exception as exc:
                # Let kill switch propagate — all other errors are best-effort
                from x_poster import KillSwitchActive
                if isinstance(exc, KillSwitchActive):
                    raise
                pass  # Non-critical — likes are best-effort

    def _maybe_draft_quote_rt(self, tweet: dict, score: int) -> List[dict]:
        """Draft a quote-RT for exceptionally relevant tweets."""
        if (score < self.quote_rt_threshold
                or not self.quote_rt_drafter
                or self._get_count('quote_rts') >= self.daily_quote_rt_budget):
            return []

        # Also check engagement metrics — only amplify content that's already resonating
        metrics = tweet.get('public_metrics', {})
        likes = metrics.get('like_count', 0)
        rts = metrics.get('retweet_count', 0)
        if likes < 10 and rts < 5:
            return []

        try:
            draft = self.quote_rt_drafter(tweet)
            if draft:
                self._increment_count('quote_rts')
                return [{'tweet_id': tweet['id'], 'text': draft}]
        except Exception as exc:
            from x_poster import KillSwitchActive
            if isinstance(exc, KillSwitchActive):
                raise
            pass

        return []

    def process_timeline(self, tweets: List[dict]) -> dict:
        """Process a batch of timeline tweets. Returns summary."""
        likes_before = self._get_count('likes')
        quote_rt_drafts = []
        top_tweets = []

        for tweet in tweets:
            score = self._score_tweet(tweet)
            if score > 0:
                self._process_tweet(tweet, score)
                drafts = self._maybe_draft_quote_rt(tweet, score)
                quote_rt_drafts.extend(drafts)

                # Collect top-scoring tweets for persistence
                if score >= 4:
                    top_tweets.append((tweet, score))

        likes_after = self._get_count('likes')

        # Persist top-scoring tweets to newsfeed-top.jsonl
        if top_tweets:
            self._persist_top_tweets(top_tweets)

        return {
            'processed': len(tweets),
            'liked': likes_after - likes_before,  # actual likes posted, not just scored
            'quote_rt_drafts': quote_rt_drafts,
        }

    def _persist_top_tweets(self, scored_tweets: List[tuple]):
        """Append high-scoring tweets to newsfeed-top.jsonl (48h rolling window)."""
        import json
        from datetime import datetime, timezone, timedelta

        sp = str(
            Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
            / ".datacore" / "state"
        )
        jsonl_file = Path(sp) / "newsfeed-top.jsonl"
        Path(sp).mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=48)

        # Load existing entries (for dedup and windowing)
        existing = {}
        if jsonl_file.exists():
            for line in jsonl_file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    tid = entry.get("tweet_id")
                    captured = datetime.fromisoformat(entry.get("captured_at", ""))
                    if captured > cutoff and tid:
                        existing[tid] = entry
                except Exception:
                    pass

        # Add new entries
        added = 0
        for tweet, score in scored_tweets:
            tid = str(tweet.get("id", ""))
            if not tid or tid in existing:
                continue
            entry = {
                "tweet_id": tid,
                "author": tweet.get("author_username", tweet.get("author_id", "")),
                "content": tweet.get("text", ""),
                "url": f"https://x.com/i/status/{tid}",
                "score": score,
                "captured_at": now.isoformat(),
            }
            existing[tid] = entry
            added += 1

        # Write back (replacing file with current 48h window)
        lines = [json.dumps(e) for e in existing.values()]
        jsonl_file.write_text("\n".join(lines) + ("\n" if lines else ""))

        if added:
            print(f"  Persisted {added} top tweets to newsfeed-top.jsonl")
