# .datacore/modules/comms/lib/follow_list_builder.py
"""Build follow list by scraping influencer networks via X API v2.

Requires X_BEARER_TOKEN env var (Basic plan: $200/mo).

Rate limits (Basic):
- GET /2/users/by/username: 300 requests / 15 min
- GET /2/users/:id/following: 15 requests / 15 min
- GET /2/users/:id/tweets: 1500 requests / 15 min (much more generous!)
- Max 1000 results per paginated request

Strategy: For each anchor INFLUENCER (not org):
  1. Scrape their following list (curated, high quality for individuals)
  2. Scrape their recent replies to find who they engage with (highest signal)
  3. Accounts found via multiple anchors rank highest (cross-cluster overlap)

Pause 65s between anchors to respect rate limits.
"""
import json
import os
import time
import urllib.parse
import yaml
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import List, Optional, Set

from follow_db import FollowDB


X_API_BASE = "https://api.x.com/2"


class FollowListBuilder:
    def __init__(self, db: FollowDB, bearer_token: str = None):
        self.db = db
        self.bearer_token = bearer_token or os.environ.get("X_BEARER_TOKEN")
        if not self.bearer_token:
            raise ValueError("X_BEARER_TOKEN required for follow list building")
        self._request_count = 0

    def build_from_anchors(self, config_path: str, max_per_anchor: int = 200,
                           only_cluster: str = None):
        """Scrape all anchor accounts and populate the follow DB."""
        with open(config_path) as f:
            config = yaml.safe_load(f)

        for cluster_name, cluster_data in config.get('clusters', {}).items():
            if only_cluster and cluster_name != only_cluster:
                print(f"\n=== Skipping cluster: {cluster_name} (--cluster filter) ===")
                continue
            print(f"\n=== Cluster: {cluster_name} ===")
            for anchor in cluster_data.get('anchors', []):
                username = anchor['username']
                print(f"  Scraping @{username}...")

                try:
                    user_id = self._resolve_username(username)
                    if not user_id:
                        print(f"    Could not resolve @{username}")
                        continue

                    # 1. Following list (curated quality for individuals)
                    following = self._get_following(user_id, max_results=max_per_anchor)
                    print(f"    Following: {len(following)} accounts")

                    for acct in following:
                        self.db.add_account(
                            user_id=acct['id'],
                            username=acct.get('username', ''),
                            display_name=acct.get('name', ''),
                            bio=acct.get('description', ''),
                            followers=acct.get('public_metrics', {}).get('followers_count', 0),
                            following=acct.get('public_metrics', {}).get('following_count', 0),
                            cluster=cluster_name,
                            source_anchor=f"@{username}",
                            overlap_count=1,
                        )

                    # 2. Reply targets (who they actively engage with)
                    reply_targets = self._get_reply_targets(user_id, max_tweets=100)
                    print(f"    Reply targets: {len(reply_targets)} accounts")

                    for acct in reply_targets:
                        self.db.add_account(
                            user_id=acct['id'],
                            username=acct.get('username', ''),
                            display_name=acct.get('name', ''),
                            bio=acct.get('description', ''),
                            followers=acct.get('public_metrics', {}).get('followers_count', 0),
                            following=acct.get('public_metrics', {}).get('following_count', 0),
                            cluster=cluster_name,
                            source_anchor=f"@{username}:reply",
                            overlap_count=1,
                        )

                    # Rate limit pause between anchors
                    time.sleep(65)

                except Exception as e:
                    print(f"    Error: {e}")
                    time.sleep(65)
                    continue

        stats = self.db.stats()
        print(f"\n=== Build complete ===")
        print(f"Total accounts: {stats['total']}")
        print(f"By cluster: {stats['by_cluster']}")

        # Show overlap stats
        self._print_overlap_stats()

    def _print_overlap_stats(self):
        """Print overlap distribution to show data quality."""
        rows = self.db.overlap_stats()
        if rows:
            print("\nOverlap distribution:")
            for oc, cnt in rows:
                print(f"  overlap={oc}: {cnt} accounts")

    def _resolve_username(self, username: str) -> Optional[str]:
        """Resolve @username to user_id."""
        url = f"{X_API_BASE}/users/by/username/{username}?user.fields=public_metrics"
        data = self._api_get(url)
        return data.get('data', {}).get('id')

    def _get_following(self, user_id: str, max_results: int = 200) -> List[dict]:
        """Get accounts a user follows (paginated)."""
        accounts = []
        url = (
            f"{X_API_BASE}/users/{user_id}/following"
            f"?max_results=1000&user.fields=public_metrics,description"
        )

        while url and len(accounts) < max_results:
            data = self._api_get(url)
            accounts.extend(data.get('data', []))

            # Pagination
            next_token = data.get('meta', {}).get('next_token')
            if next_token:
                base = url.split('&pagination_token=')[0]
                url = f"{base}&pagination_token={urllib.parse.quote(next_token, safe='')}"
            else:
                break

        return accounts[:max_results]

    def _get_reply_targets(self, user_id: str, max_tweets: int = 100) -> List[dict]:
        """Get accounts this user replies to (highest engagement signal).

        Fetches recent tweets, filters for replies, extracts unique
        reply-to users, then resolves their profiles.
        """
        # Get recent tweets including replies
        url = (
            f"{X_API_BASE}/users/{user_id}/tweets"
            f"?max_results=100"
            f"&tweet.fields=in_reply_to_user_id,referenced_tweets"
            f"&exclude=retweets"
        )

        reply_user_ids: Set[str] = set()
        tweets_fetched = 0

        while url and tweets_fetched < max_tweets:
            data = self._api_get(url)
            tweets = data.get('data', [])
            tweets_fetched += len(tweets)

            for tweet in tweets:
                reply_to = tweet.get('in_reply_to_user_id')
                if reply_to and reply_to != user_id:  # Skip self-replies (threads)
                    reply_user_ids.add(reply_to)

            next_token = data.get('meta', {}).get('next_token')
            if next_token and tweets_fetched < max_tweets:
                base = url.split('&pagination_token=')[0]
                url = f"{base}&pagination_token={urllib.parse.quote(next_token, safe='')}"
            else:
                break

        if not reply_user_ids:
            return []

        # Batch resolve user profiles (up to 100 per request)
        return self._batch_lookup_users(list(reply_user_ids)[:100])

    def _batch_lookup_users(self, user_ids: List[str]) -> List[dict]:
        """Lookup user profiles by ID in batches of 100."""
        all_users = []
        for i in range(0, len(user_ids), 100):
            batch = user_ids[i:i+100]
            ids_str = ','.join(batch)
            url = f"{X_API_BASE}/users?ids={ids_str}&user.fields=public_metrics,description"
            data = self._api_get(url)
            all_users.extend(data.get('data', []))
        return all_users

    def _api_get(self, url: str, _retries: int = 0) -> dict:
        """Make authenticated GET request to X API v2. Max 3 retries on 429."""
        req = Request(url)
        req.add_header("Authorization", f"Bearer {self.bearer_token}")
        self._request_count += 1

        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 429 and _retries < 3:
                retry_after = int(e.headers.get('Retry-After', 60))
                wait = min(retry_after + 5, 900)  # Cap at 15 min
                print(f"    Rate limited. Waiting {wait}s (retry {_retries + 1}/3)...")
                time.sleep(wait)
                return self._api_get(url, _retries=_retries + 1)
            error_body = e.read().decode()
            if e.code == 402:
                raise SystemExit(f"CREDITS DEPLETED — stopping. {error_body}")
            raise Exception(f"X API {e.code}: {error_body}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    from env_utils import load_env_files

    DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
    db_path = DATA_DIR / ".datacore" / "state" / "follow-list.db"
    config_path = DATA_DIR / ".datacore" / "modules" / "comms" / "config" / "anchor-accounts.yaml"

    # Load env
    load_env_files()

    db = FollowDB(db_path)
    builder = FollowListBuilder(db)

    max_per = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    only_cluster = sys.argv[2] if len(sys.argv) > 2 else None
    builder.build_from_anchors(str(config_path), max_per_anchor=max_per,
                               only_cluster=only_cluster)
    db.close()
