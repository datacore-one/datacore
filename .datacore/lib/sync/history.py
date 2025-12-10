"""
Sync History - Tracks sync operations for audit and debugging.

DIP-0010: Task Sync Architecture
"""

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SyncHistoryEntry:
    """A sync history entry."""
    id: Optional[int]
    timestamp: datetime
    direction: str  # "pull" or "push"
    adapter: str
    items_processed: int
    items_created: int
    items_updated: int
    items_failed: int
    errors: str  # JSON string of errors
    duration_ms: int


class SyncHistory:
    """
    Tracks sync operations in SQLite database.

    Uses existing zettel_db pattern for consistency.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize sync history.

        Args:
            db_path: Path to database. If None, uses ~/.datacore/sync_history.db
        """
        if db_path:
            self.db_path = Path(db_path)
        else:
            data_dir = Path(os.environ.get("DATA_DIR", os.path.expanduser("~/Data")))
            self.db_path = data_dir / ".datacore" / "state" / "sync_history.db"

        self._ensure_db()

    def _ensure_db(self):
        """Ensure database and tables exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    items_processed INTEGER DEFAULT 0,
                    items_created INTEGER DEFAULT 0,
                    items_updated INTEGER DEFAULT 0,
                    items_failed INTEGER DEFAULT 0,
                    errors TEXT,
                    duration_ms INTEGER DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_history_timestamp
                ON sync_history(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_history_adapter
                ON sync_history(adapter)
            """)

            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record(
        self,
        direction: str,
        adapter: str,
        items_processed: int = 0,
        items_created: int = 0,
        items_updated: int = 0,
        items_failed: int = 0,
        errors: List[str] = None,
        duration_ms: int = 0
    ) -> int:
        """
        Record a sync operation.

        Returns:
            ID of the new record.
        """
        import json

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO sync_history
                (timestamp, direction, adapter, items_processed, items_created,
                 items_updated, items_failed, errors, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                direction,
                adapter,
                items_processed,
                items_created,
                items_updated,
                items_failed,
                json.dumps(errors or []),
                duration_ms
            ))
            conn.commit()
            return cursor.lastrowid

    def get_last_sync(self, adapter: str = None, direction: str = None) -> Optional[datetime]:
        """
        Get timestamp of last sync.

        Args:
            adapter: Filter by adapter name
            direction: Filter by direction (pull/push)

        Returns:
            Datetime of last sync or None
        """
        query = "SELECT timestamp FROM sync_history WHERE 1=1"
        params = []

        if adapter:
            query += " AND adapter = ?"
            params.append(adapter)

        if direction:
            query += " AND direction = ?"
            params.append(direction)

        query += " ORDER BY timestamp DESC LIMIT 1"

        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            if row:
                return datetime.fromisoformat(row["timestamp"])
            return None

    def get_history(
        self,
        days: int = 7,
        adapter: str = None,
        direction: str = None,
        limit: int = 100
    ) -> List[SyncHistoryEntry]:
        """
        Get sync history entries.

        Args:
            days: Number of days to look back
            adapter: Filter by adapter
            direction: Filter by direction
            limit: Max entries to return

        Returns:
            List of SyncHistoryEntry
        """
        import json
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        query = "SELECT * FROM sync_history WHERE timestamp > ?"
        params = [cutoff]

        if adapter:
            query += " AND adapter = ?"
            params.append(adapter)

        if direction:
            query += " AND direction = ?"
            params.append(direction)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        entries = []
        with self._get_connection() as conn:
            for row in conn.execute(query, params):
                entries.append(SyncHistoryEntry(
                    id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    direction=row["direction"],
                    adapter=row["adapter"],
                    items_processed=row["items_processed"],
                    items_created=row["items_created"],
                    items_updated=row["items_updated"],
                    items_failed=row["items_failed"],
                    errors=row["errors"],
                    duration_ms=row["duration_ms"]
                ))

        return entries

    def get_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Get sync statistics for diagnostic.

        Args:
            days: Number of days to look back

        Returns:
            Dict with statistics
        """
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        stats = {
            "period_days": days,
            "pull": {"success": 0, "failed": 0, "items": 0},
            "push": {"success": 0, "failed": 0, "items": 0},
            "by_adapter": {},
            "last_sync": None,
            "errors": []
        }

        with self._get_connection() as conn:
            # Aggregate by direction
            for row in conn.execute("""
                SELECT
                    direction,
                    COUNT(*) as count,
                    SUM(items_processed) as items,
                    SUM(CASE WHEN items_failed > 0 THEN 1 ELSE 0 END) as failed
                FROM sync_history
                WHERE timestamp > ?
                GROUP BY direction
            """, (cutoff,)):
                direction = row["direction"]
                stats[direction]["success"] = row["count"] - row["failed"]
                stats[direction]["failed"] = row["failed"]
                stats[direction]["items"] = row["items"] or 0

            # By adapter
            for row in conn.execute("""
                SELECT
                    adapter,
                    COUNT(*) as count,
                    SUM(items_processed) as items
                FROM sync_history
                WHERE timestamp > ?
                GROUP BY adapter
            """, (cutoff,)):
                stats["by_adapter"][row["adapter"]] = {
                    "count": row["count"],
                    "items": row["items"] or 0
                }

            # Last sync
            row = conn.execute("""
                SELECT timestamp FROM sync_history
                ORDER BY timestamp DESC LIMIT 1
            """).fetchone()
            if row:
                stats["last_sync"] = row["timestamp"]

            # Recent errors
            for row in conn.execute("""
                SELECT errors FROM sync_history
                WHERE timestamp > ? AND errors != '[]'
                ORDER BY timestamp DESC LIMIT 10
            """, (cutoff,)):
                import json
                errors = json.loads(row["errors"])
                stats["errors"].extend(errors)

        return stats

    def set_state(self, key: str, value: str):
        """Store sync state value."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sync_state (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
            conn.commit()

    def get_state(self, key: str) -> Optional[str]:
        """Get sync state value."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = ?",
                (key,)
            ).fetchone()
            return row["value"] if row else None

    def cleanup(self, days: int = 30):
        """
        Remove old history entries.

        Args:
            days: Remove entries older than this
        """
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM sync_history WHERE timestamp < ?",
                (cutoff,)
            )
            conn.commit()
