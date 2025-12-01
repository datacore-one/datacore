"""SQLite database for follow list management.

Stores follow targets with cluster classification, overlap scoring,
and lifecycle tracking (pending → followed → mutual / unfollowed).

Implements weak ties theory: daily batches are cluster-diverse,
ensuring follows span different network segments.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional


class FollowDB:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                bio TEXT DEFAULT '',
                followers INTEGER DEFAULT 0,
                following INTEGER DEFAULT 0,
                cluster TEXT NOT NULL,
                source_anchor TEXT DEFAULT '',
                overlap_count INTEGER DEFAULT 1,
                score REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending',
                added_at TEXT NOT NULL,
                followed_at TEXT,
                follow_back_at TEXT,
                unfollowed_at TEXT,
                last_engaged TEXT,
                engagement_count INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
            CREATE INDEX IF NOT EXISTS idx_cluster ON accounts(cluster);
            CREATE INDEX IF NOT EXISTS idx_score ON accounts(score DESC);

            CREATE TABLE IF NOT EXISTS review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT DEFAULT '',
                reviewed_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def add_account(self, user_id: str, username: str, cluster: str,
                    source_anchor: str, overlap_count: int = 1,
                    display_name: str = '', bio: str = '',
                    followers: int = 0, following: int = 0):
        """Add or update a follow target."""
        now = datetime.now(timezone.utc).isoformat()
        import math
        score = overlap_count * math.log(max(followers, 10))

        try:
            self._conn.execute(
                """INSERT INTO accounts
                   (user_id, username, display_name, bio, followers, following,
                    cluster, source_anchor, overlap_count, score, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, display_name, bio, followers, following,
                 cluster, source_anchor, overlap_count, score, now),
            )
        except sqlite3.IntegrityError:
            import math as _math
            log_followers = _math.log(max(followers, 10))
            self._conn.execute(
                """UPDATE accounts SET
                   overlap_count = overlap_count + 1,
                   score = (overlap_count + 1) * ?,
                   source_anchor = source_anchor || ',' || ?,
                   followers = ?,
                   following = ?
                   WHERE user_id = ?""",
                (log_followers, source_anchor, followers, following, user_id),
            )
        self._conn.commit()

    def get_account(self, user_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_top_targets(self, limit: int = 100, status: str = 'pending') -> List[dict]:
        """Get highest-scored pending targets."""
        rows = self._conn.execute(
            "SELECT * FROM accounts WHERE status = ? ORDER BY score DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_batch(self, size: int = 20) -> List[dict]:
        """Get a cluster-diverse batch for daily follows.

        Implements weak ties: distributes follows across clusters
        proportionally, ensuring diverse network growth.
        """
        clusters = self._conn.execute(
            """SELECT cluster, COUNT(*) as cnt FROM accounts
               WHERE status = 'pending' GROUP BY cluster"""
        ).fetchall()

        if not clusters:
            return []

        total_pending = sum(c['cnt'] for c in clusters)
        batch = []
        for cluster_row in clusters:
            cluster = cluster_row['cluster']
            quota = max(1, round(size * cluster_row['cnt'] / total_pending))
            rows = self._conn.execute(
                """SELECT * FROM accounts
                   WHERE status = 'pending' AND cluster = ?
                   ORDER BY score DESC LIMIT ?""",
                (cluster, quota),
            ).fetchall()
            batch.extend(dict(r) for r in rows)

        batch.sort(key=lambda x: x['score'], reverse=True)
        return batch[:size]

    def mark_followed(self, user_id: str, followed_at: str = None):
        now = followed_at or datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE accounts SET status = 'followed', followed_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        self._conn.commit()

    def mark_follow_back(self, user_id: str):
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE accounts SET status = 'mutual', follow_back_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        self._conn.commit()

    def mark_unfollowed(self, user_id: str, reason: str = ''):
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE accounts SET status = 'unfollowed', unfollowed_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        self._conn.execute(
            "INSERT INTO review_log (user_id, action, reason, reviewed_at) VALUES (?, 'unfollow', ?, ?)",
            (user_id, reason, now),
        )
        self._conn.commit()

    def get_stale_follows(self, days: int = 30) -> List[dict]:
        """Get accounts followed > N days ago with no follow-back and no engagement."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM accounts
               WHERE status = 'followed'
               AND followed_at < ?
               AND engagement_count = 0
               ORDER BY followed_at ASC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_engagement(self, user_id: str):
        """Record that we engaged with this account (replied, liked)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE accounts SET
               last_engaged = ?,
               engagement_count = engagement_count + 1
               WHERE user_id = ?""",
            (now, user_id),
        )
        self._conn.commit()

    def overlap_stats(self) -> list:
        """Return overlap distribution as list of (overlap_count, num_accounts) tuples."""
        rows = self._conn.execute(
            "SELECT overlap_count, COUNT(*) FROM accounts GROUP BY overlap_count ORDER BY overlap_count DESC"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        by_status = {}
        for row in self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM accounts GROUP BY status"
        ).fetchall():
            by_status[row['status']] = row['cnt']
        by_cluster = {}
        for row in self._conn.execute(
            "SELECT cluster, COUNT(*) as cnt FROM accounts GROUP BY cluster"
        ).fetchall():
            by_cluster[row['cluster']] = row['cnt']
        return {
            'total': total,
            'by_status': by_status,
            'by_cluster': by_cluster,
        }

    def close(self):
        self._conn.close()
