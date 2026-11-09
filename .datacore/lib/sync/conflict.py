"""
Conflict queue and configuration for Task Sync.

DIP-0010: Task Sync Architecture - Phase 2

What remains here: the conflict record (`Conflict`, `ConflictField`), the
human-review queue (`ConflictQueue`, persisted in sync_history.db, with its
CLI), the strategy vocabulary and the configuration loader.

ConflictDetector and ConflictResolver were REMOVED on 2026-09-23 (owner
decision P7). The detector was two-way: it compared org against external and
never read `last_sync`, so a change made on only one side was reported as a
conflict, and with the default ORG_WINS for state the resolver would have
reopened an issue a human closed on GitHub. The data model holds no per-task
base snapshot, so a correct three-way rule could not be wired in, and nothing
live called them (SyncEngine.sync is a stub). When a sync engine is built,
build detection to the specification in
.datacore/specs/datacore-lean/DatacoreSpec/Reconcile.lean, `resolve3`: take
the value both sides agree on; if only one side changed since the base, take
that side; consult the configured strategy only when both changed and differ
(`resolve3_respects_one_sided`, `resolve3_strategy_only_on_conflict`). That
needs the base value stored at each successful sync.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .adapters.base import OrgTask, ExternalTask, TaskState, Priority
except ImportError:
    from adapters.base import OrgTask, ExternalTask, TaskState, Priority


class ConflictType(Enum):
    """Types of conflicts that can occur."""
    STATE = "state"           # Task state changed in both places
    TITLE = "title"           # Title changed in both places
    DESCRIPTION = "description"  # Body/description changed
    PRIORITY = "priority"     # Priority changed in both places
    DEADLINE = "deadline"     # Deadline changed in both places
    LABELS = "labels"         # Labels/tags changed in both places
    COMMENTS = "comments"     # New comments on external (info only)


class ConflictStrategy(Enum):
    """Strategies for resolving conflicts."""
    ORG_WINS = "org_wins"       # Org-mode changes overwrite external
    EXTERNAL_WINS = "external_wins"  # External changes overwrite org-mode
    MERGE = "merge"            # Attempt automatic merge
    ASK = "ask"                # Add to conflict queue for human decision


@dataclass
class ConflictField:
    """Represents a conflict in a specific field."""
    field_name: str
    conflict_type: ConflictType
    org_value: Any
    external_value: Any
    last_synced_value: Optional[Any] = None


@dataclass
class Conflict:
    """Represents a detected conflict between org and external task."""
    id: Optional[int] = None
    external_id: str = ""          # e.g., "github:owner/repo#42"
    org_task_id: str = ""          # Org task identifier
    detected_at: datetime = field(default_factory=datetime.now)

    # The tasks involved
    org_task: Optional[OrgTask] = None
    external_task: Optional[ExternalTask] = None

    # Specific conflicts
    fields: List[ConflictField] = field(default_factory=list)

    # Resolution
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolution_strategy: Optional[ConflictStrategy] = None
    resolved_by: str = ""  # "auto" or "human"

    @property
    def conflict_types(self) -> List[ConflictType]:
        """Get list of conflict types."""
        return [f.conflict_type for f in self.fields]

    @property
    def summary(self) -> str:
        """Get human-readable summary."""
        types = ", ".join(ct.value for ct in self.conflict_types)
        return f"Conflict in {self.external_id}: {types}"


class ConflictQueue:
    """
    Stores unresolved conflicts for human review.

    Conflicts with ASK strategy or failed merges are added here
    and surfaced in /today briefing.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize conflict queue.

        Args:
            db_path: Path to database. If None, uses default location.
        """
        if db_path:
            self.db_path = Path(db_path)
        else:
            data_dir = Path(os.environ.get("DATA_DIR") or os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
            self.db_path = data_dir / ".datacore" / "state" / "sync_history.db"

        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure conflict tables exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT NOT NULL,
                    org_task_id TEXT,
                    detected_at TEXT NOT NULL,
                    conflict_data TEXT NOT NULL,
                    resolved INTEGER DEFAULT 0,
                    resolved_at TEXT,
                    resolution_strategy TEXT,
                    resolved_by TEXT,
                    resolution_notes TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conflicts_unresolved
                ON sync_conflicts(resolved, detected_at DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conflicts_external_id
                ON sync_conflicts(external_id)
            """)

            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def add(self, conflict: Conflict) -> int:
        """
        Add conflict to queue.

        Returns:
            ID of the conflict record
        """
        conflict_data = {
            "fields": [
                {
                    "field_name": f.field_name,
                    "conflict_type": f.conflict_type.value,
                    "org_value": f.org_value,
                    "external_value": f.external_value,
                    "last_synced_value": f.last_synced_value,
                }
                for f in conflict.fields
            ],
            "snapshot_version": 1,
            "org_task": asdict(conflict.org_task) if conflict.org_task else None,
            "external_task": asdict(conflict.external_task) if conflict.external_task else None,
        }

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO sync_conflicts
                (external_id, org_task_id, detected_at, conflict_data)
                VALUES (?, ?, ?, ?)
            """, (
                conflict.external_id,
                conflict.org_task_id,
                conflict.detected_at.isoformat(),
                json.dumps(conflict_data, default=_snapshot_json)
            ))
            conn.commit()
            conflict.id = cursor.lastrowid
            return cursor.lastrowid

    def get_unresolved(self, limit: int = 50) -> List[Conflict]:
        """
        Get unresolved conflicts.

        Args:
            limit: Max number to return

        Returns:
            List of unresolved Conflict objects
        """
        conflicts = []

        with self._get_connection() as conn:
            for row in conn.execute("""
                SELECT * FROM sync_conflicts
                WHERE resolved = 0
                ORDER BY detected_at DESC
                LIMIT ?
            """, (limit,)):
                conflict = self._row_to_conflict(row)
                conflicts.append(conflict)

        return conflicts

    def get_by_external_id(self, external_id: str) -> Optional[Conflict]:
        """Get most recent conflict for an external ID."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM sync_conflicts
                WHERE external_id = ?
                ORDER BY detected_at DESC
                LIMIT 1
            """, (external_id,)).fetchone()

            if row:
                return self._row_to_conflict(row)

        return None

    def _row_to_conflict(self, row) -> Conflict:
        """Convert database row to Conflict object."""
        data = json.loads(row["conflict_data"])

        fields = [
            ConflictField(
                field_name=f["field_name"],
                conflict_type=ConflictType(f["conflict_type"]),
                org_value=f["org_value"],
                external_value=f["external_value"],
                last_synced_value=f.get("last_synced_value"),
            )
            for f in data.get("fields", [])
        ]

        return Conflict(
            id=row["id"],
            external_id=row["external_id"],
            org_task_id=row["org_task_id"],
            detected_at=datetime.fromisoformat(row["detected_at"]),
            fields=fields,
            org_task=_restore_task(data.get("org_task"), OrgTask) if data.get("snapshot_version") == 1 else None,
            external_task=_restore_task(data.get("external_task"), ExternalTask) if data.get("snapshot_version") == 1 else None,
            resolved=bool(row["resolved"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
            resolution_strategy=ConflictStrategy(row["resolution_strategy"]) if row["resolution_strategy"] else None,
            resolved_by=row["resolved_by"] or "",
        )

    def resolve(
        self,
        conflict_id: int,
        strategy: ConflictStrategy,
        resolved_by: str = "human",
        notes: str = ""
    ) -> bool:
        """
        Mark conflict as resolved.

        Args:
            conflict_id: ID of conflict to resolve
            strategy: Strategy used for resolution
            resolved_by: "human" or "auto"
            notes: Resolution notes

        Returns:
            True if updated successfully
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE sync_conflicts
                SET resolved = 1,
                    resolved_at = ?,
                    resolution_strategy = ?,
                    resolved_by = ?,
                    resolution_notes = ?
                WHERE id = ?
            """, (
                datetime.now().isoformat(),
                strategy.value,
                resolved_by,
                notes,
                conflict_id
            ))
            conn.commit()
            return cursor.rowcount > 0

    def get_stats(self) -> Dict[str, Any]:
        """Get conflict statistics for diagnostic."""
        stats = {
            "unresolved": 0,
            "resolved_today": 0,
            "by_type": {},
            "oldest_unresolved": None,
        }

        with self._get_connection() as conn:
            # Unresolved count
            row = conn.execute(
                "SELECT COUNT(*) as count FROM sync_conflicts WHERE resolved = 0"
            ).fetchone()
            stats["unresolved"] = row["count"]

            # Resolved today
            today = datetime.now().date().isoformat()
            row = conn.execute("""
                SELECT COUNT(*) as count FROM sync_conflicts
                WHERE resolved = 1 AND resolved_at LIKE ?
            """, (f"{today}%",)).fetchone()
            stats["resolved_today"] = row["count"]

            # By conflict type
            for row in conn.execute("""
                SELECT conflict_data FROM sync_conflicts WHERE resolved = 0
            """):
                data = json.loads(row["conflict_data"])
                for field in data.get("fields", []):
                    ctype = field.get("conflict_type", "unknown")
                    stats["by_type"][ctype] = stats["by_type"].get(ctype, 0) + 1

            # Oldest unresolved
            row = conn.execute("""
                SELECT detected_at FROM sync_conflicts
                WHERE resolved = 0
                ORDER BY detected_at ASC
                LIMIT 1
            """).fetchone()
            if row:
                stats["oldest_unresolved"] = row["detected_at"]

        return stats

    def cleanup(self, days: int = 30):
        """Remove old resolved conflicts."""
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            conn.execute("""
                DELETE FROM sync_conflicts
                WHERE resolved = 1 AND resolved_at < ?
            """, (cutoff,))
            conn.commit()


def _snapshot_json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if value is Priority.NONE:
        return "NONE"
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported conflict snapshot type: {type(value).__name__}")


def _restore_task(snapshot, task_type):
    if snapshot is None:
        return None
    values = dict(snapshot)
    for key in ("sync_updated", "deadline", "scheduled", "created_at", "updated_at", "due_date"):
        if values.get(key) is not None:
            values[key] = datetime.fromisoformat(values[key])
    if task_type is OrgTask:
        values["state"] = TaskState(values["state"])
        if values.get("priority") is not None:
            values["priority"] = Priority.NONE if values["priority"] == "NONE" else Priority(values["priority"])
    return task_type(**values)


def load_conflict_config(data_dir=None, *, settings=None) -> Dict[str, ConflictStrategy]:
    """Use the same layered configuration as the engine; invalid rules fail."""
    from sync.config import load_settings
    root = Path(data_dir or os.environ.get("DATA_DIR") or os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
    if settings is None:
        settings = load_settings(root)
    config = settings.get("sync", {}).get("conflict_resolution", {})
    if not isinstance(config, dict):
        raise ValueError("conflict_resolution must be a mapping")
    return {key: ConflictStrategy(value) for key, value in config.items()}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sync Conflict Management")
    parser.add_argument("--unresolved", action="store_true",
                        help="List unresolved conflicts")
    parser.add_argument("--stats", action="store_true",
                        help="Show conflict statistics")
    parser.add_argument("--resolve", type=int, metavar="ID",
                        help="Resolve conflict by ID")
    parser.add_argument("--strategy", choices=["org_wins", "external_wins"],
                        help="Strategy for --resolve")

    args = parser.parse_args()

    queue = ConflictQueue()

    if args.unresolved:
        conflicts = queue.get_unresolved()
        if not conflicts:
            print("No unresolved conflicts.")
        else:
            print(f"Unresolved Conflicts ({len(conflicts)}):")
            print("-" * 50)
            for c in conflicts:
                types = ", ".join(f.conflict_type.value for f in c.fields)
                print(f"[{c.id}] {c.external_id}")
                print(f"    Types: {types}")
                print(f"    Detected: {c.detected_at.strftime('%Y-%m-%d %H:%M')}")
                for f in c.fields:
                    print(f"    - {f.field_name}: org={f.org_value}, external={f.external_value}")
                print()

    elif args.stats:
        stats = queue.get_stats()
        print("Conflict Statistics:")
        print("-" * 30)
        print(f"  Unresolved: {stats['unresolved']}")
        print(f"  Resolved today: {stats['resolved_today']}")
        if stats['oldest_unresolved']:
            print(f"  Oldest unresolved: {stats['oldest_unresolved']}")
        if stats['by_type']:
            print("  By type:")
            for ctype, count in stats['by_type'].items():
                print(f"    - {ctype}: {count}")

    elif args.resolve:
        if not args.strategy:
            print("Error: --strategy required with --resolve")
            exit(1)
        strategy = ConflictStrategy(args.strategy)
        success = queue.resolve(args.resolve, strategy, resolved_by="cli")
        if success:
            print(f"Conflict {args.resolve} resolved with strategy: {args.strategy}")
        else:
            print(f"Failed to resolve conflict {args.resolve}")

    else:
        parser.print_help()
