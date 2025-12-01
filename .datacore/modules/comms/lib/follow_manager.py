# .datacore/modules/comms/lib/follow_manager.py
"""Follow manager — daily follow execution and periodic review.

Runs as Nightshift task. Executes cluster-diverse follow batches,
checks for follow-backs, and prunes stale follows.

Schedule:
  - Daily: execute_daily_follows() — follow batch from DB
  - Weekly: review_stale_follows() — unfollow non-reciprocal after 30 days
  - Weekly: check_follow_backs() — update mutual status via X API v2
"""
import json
import os
import time
import urllib.parse
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from follow_db import FollowDB


class FollowManager:
    def __init__(self, db: FollowDB, poster, daily_budget: int = 20,
                 bearer_token: str = None):
        self.db = db
        self.poster = poster
        self.daily_budget = daily_budget
        self.bearer_token = bearer_token or os.environ.get("X_BEARER_TOKEN")

    def execute_daily_follows(self) -> dict:
        """Follow a cluster-diverse batch of accounts."""
        batch = self.db.get_daily_batch(size=self.daily_budget)
        followed = 0
        errors = 0

        for acct in batch:
            try:
                self.poster.follow(acct['user_id'])
                self.db.mark_followed(acct['user_id'])
                followed += 1
                # Stagger: 15-20s between follows to look organic
                time.sleep(18)
            except Exception as e:
                errors += 1
                if 'rate limit' in str(e).lower() or '429' in str(e):
                    print(f"Rate limited at {followed} follows. Stopping.")
                    break
                print(f"  Error following @{acct['username']}: {e}")

        return {
            'followed': followed,
            'errors': errors,
            'remaining_pending': self.db.stats().get('by_status', {}).get('pending', 0),
        }

    def review_stale_follows(self, stale_days: int = 30) -> dict:
        """Unfollow accounts that didn't follow back within N days."""
        stale = self.db.get_stale_follows(days=stale_days)
        unfollowed = 0

        for acct in stale:
            try:
                self.poster.unfollow(acct['user_id'])
                self.db.mark_unfollowed(
                    acct['user_id'],
                    reason=f"No follow-back after {stale_days} days",
                )
                unfollowed += 1
                time.sleep(10)
            except Exception as e:
                print(f"  Error unfollowing @{acct['username']}: {e}")

        return {'unfollowed': unfollowed, 'reviewed': len(stale)}

    def check_follow_backs(self, our_user_id: str) -> dict:
        """Check which accounts we follow have followed us back.

        Uses X API v2 GET /2/users/:id/followers to get our follower list,
        then cross-references with 'followed' status accounts in DB.
        Requires bearer_token.
        """
        if not self.bearer_token:
            return {'error': 'No bearer token', 'updated': 0}

        # Get our follower IDs (paginated)
        follower_ids = set()
        url = (
            f"https://api.x.com/2/users/{our_user_id}/followers"
            f"?max_results=1000&user.fields=id"
        )
        page_retries = 0  # retries for current page (reset per page)
        complete = True
        while url:
            req = Request(url)
            req.add_header("Authorization", f"Bearer {self.bearer_token}")
            try:
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                page_retries = 0  # reset on success
                for user in data.get('data', []):
                    follower_ids.add(user['id'])
                next_token = data.get('meta', {}).get('next_token')
                if next_token:
                    base = url.split('&pagination_token=')[0]
                    url = f"{base}&pagination_token={urllib.parse.quote(next_token, safe='')}"
                else:
                    url = None
            except HTTPError as e:
                if e.code == 429 and page_retries < 3:
                    page_retries += 1
                    time.sleep(65)
                    continue
                complete = False
                break

        # Cross-reference with our followed accounts
        followed = self.db.get_top_targets(limit=10000, status='followed')
        updated = 0
        for acct in followed:
            if acct['user_id'] in follower_ids:
                self.db.mark_follow_back(acct['user_id'])
                updated += 1

        return {'checked': len(followed), 'updated': updated,
                'our_followers': len(follower_ids), 'complete': complete}

    def generate_report(self) -> str:
        """Generate human-readable follow stats for /today hook."""
        stats = self.db.stats()
        lines = ["**Follow List:**"]
        total = stats['total']
        by_status = stats.get('by_status', {})
        lines.append(
            f"  {total} accounts | "
            f"{by_status.get('pending', 0)} pending | "
            f"{by_status.get('followed', 0)} followed | "
            f"{by_status.get('mutual', 0)} mutual"
        )
        by_cluster = stats.get('by_cluster', {})
        if by_cluster:
            cluster_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_cluster.items()))
            lines.append(f"  Clusters: {cluster_str}")
        return "\n".join(lines)
