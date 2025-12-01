"""
Conflict Detection and Resolution for Task Sync.

DIP-0010: Task Sync Architecture - Phase 2

Detects when both org-mode and external tools have changed since last sync,
and resolves conflicts based on configurable strategies.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
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


@dataclass
class ConflictResolution:
    """Result of conflict resolution."""
    conflict: Conflict
    strategy_used: ConflictStrategy
    org_changes: Dict[str, Any] = field(default_factory=dict)  # Changes to apply to org
    external_changes: Dict[str, Any] = field(default_factory=dict)  # Changes to push to external
    needs_human_review: bool = False
    notes: str = ""


class ConflictDetector:
    """
    Detects conflicts between org-mode tasks and external tasks.

    A conflict occurs when both the org task and external task have changed
    since the last sync timestamp.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize detector.

        Args:
            config: Configuration dict with thresholds and rules
        """
        self.config = config or {}
        # Fields to check for conflicts
        self.tracked_fields = [
            ConflictType.STATE,
            ConflictType.TITLE,
            ConflictType.DESCRIPTION,
            ConflictType.PRIORITY,
            ConflictType.DEADLINE,
            ConflictType.LABELS,
        ]

    def detect(
        self,
        org_task: OrgTask,
        external_task: ExternalTask,
        last_sync: Optional[datetime] = None
    ) -> Optional[Conflict]:
        """
        Detect conflicts between org and external task.

        Args:
            org_task: The org-mode task
            external_task: The external task
            last_sync: Timestamp of last sync (for change detection)

        Returns:
            Conflict if conflicts detected, None otherwise
        """
        conflicts = []

        # Check each field for conflicts
        # State conflict
        state_conflict = self._check_state_conflict(org_task, external_task)
        if state_conflict:
            conflicts.append(state_conflict)

        # Title conflict
        title_conflict = self._check_title_conflict(org_task, external_task)
        if title_conflict:
            conflicts.append(title_conflict)

        # Description/body conflict
        desc_conflict = self._check_description_conflict(org_task, external_task)
        if desc_conflict:
            conflicts.append(desc_conflict)

        # Priority conflict
        priority_conflict = self._check_priority_conflict(org_task, external_task)
        if priority_conflict:
            conflicts.append(priority_conflict)

        # Deadline conflict
        deadline_conflict = self._check_deadline_conflict(org_task, external_task)
        if deadline_conflict:
            conflicts.append(deadline_conflict)

        if not conflicts:
            return None

        return Conflict(
            external_id=org_task.external_id or f"external:{external_task.id}",
            org_task_id=org_task.id,
            org_task=org_task,
            external_task=external_task,
            fields=conflicts
        )

    def _check_state_conflict(
        self,
        org_task: OrgTask,
        external_task: ExternalTask
    ) -> Optional[ConflictField]:
        """Check for state conflicts."""
        org_state = org_task.state
        external_state = external_task.state.lower()

        # Map external state to comparable format
        external_closed = external_state in ("closed", "done", "completed", "resolved")
        org_closed = org_state in (TaskState.DONE, TaskState.CANCELLED)

        # Conflict if one is closed and other is open
        if org_closed != external_closed:
            return ConflictField(
                field_name="state",
                conflict_type=ConflictType.STATE,
                org_value=org_state.value,
                external_value=external_state
            )

        return None

    def _check_title_conflict(
        self,
        org_task: OrgTask,
        external_task: ExternalTask
    ) -> Optional[ConflictField]:
        """Check for title conflicts."""
        org_title = org_task.title.strip()
        external_title = external_task.title.strip()

        # Normalize for comparison (remove common prefixes like [TEST])
        org_normalized = self._normalize_title(org_title)
        external_normalized = self._normalize_title(external_title)

        if org_normalized != external_normalized:
            return ConflictField(
                field_name="title",
                conflict_type=ConflictType.TITLE,
                org_value=org_title,
                external_value=external_title
            )

        return None

    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison."""
        import re
        # Remove common prefixes like [TEST], [WIP], etc.
        title = re.sub(r'^\[.*?\]\s*', '', title)
        return title.lower().strip()

    def _check_description_conflict(
        self,
        org_task: OrgTask,
        external_task: ExternalTask
    ) -> Optional[ConflictField]:
        """Check for description/body conflicts."""
        org_body = (org_task.body or "").strip()
        external_body = (external_task.body or "").strip()

        # Use content hash for comparison
        org_hash = self._content_hash(org_body)
        external_hash = self._content_hash(external_body)

        if org_hash != external_hash:
            # Only report if both have content (not just one-way update)
            if org_body and external_body:
                return ConflictField(
                    field_name="description",
                    conflict_type=ConflictType.DESCRIPTION,
                    org_value=org_body[:200] + "..." if len(org_body) > 200 else org_body,
                    external_value=external_body[:200] + "..." if len(external_body) > 200 else external_body
                )

        return None

    def _content_hash(self, content: str) -> str:
        """Generate hash for content comparison."""
        # Normalize whitespace
        normalized = " ".join(content.split())
        return hashlib.md5(normalized.encode()).hexdigest()

    def _check_priority_conflict(
        self,
        org_task: OrgTask,
        external_task: ExternalTask
    ) -> Optional[ConflictField]:
        """Check for priority conflicts."""
        org_priority = org_task.priority

        # Map external labels to priority
        external_priority = self._extract_priority_from_labels(external_task.labels)

        if org_priority != external_priority:
            return ConflictField(
                field_name="priority",
                conflict_type=ConflictType.PRIORITY,
                org_value=org_priority.value if org_priority else None,
                external_value=external_priority.value if external_priority else None
            )

        return None

    def _extract_priority_from_labels(self, labels: List[str]) -> Optional[Priority]:
        """Extract priority from external labels."""
        for label in labels:
            label_lower = label.lower()
            if "high" in label_lower or label == "p1" or label == "priority-high":
                return Priority.A
            if "medium" in label_lower or label == "p2" or label == "priority-medium":
                return Priority.B
            if "low" in label_lower or label == "p3" or label == "priority-low":
                return Priority.C
        return None

    def _check_deadline_conflict(
        self,
        org_task: OrgTask,
        external_task: ExternalTask
    ) -> Optional[ConflictField]:
        """Check for deadline conflicts."""
        org_deadline = org_task.deadline
        external_deadline = external_task.due_date

        # Compare dates (ignoring time)
        org_date = org_deadline.date() if org_deadline else None
        external_date = external_deadline.date() if external_deadline else None

        if org_date != external_date:
            # Only conflict if both have deadlines
            if org_deadline and external_deadline:
                return ConflictField(
                    field_name="deadline",
                    conflict_type=ConflictType.DEADLINE,
                    org_value=str(org_date),
                    external_value=str(external_date)
                )

        return None


class ConflictResolver:
    """
    Resolves conflicts based on configured strategies.

    Default strategies (from DIP-0010):
    - state: org_wins (agent decisions are authoritative)
    - description: merge (try to combine)
    - comments: external_wins (human discussion wins)
    - priority: org_wins (agent triage is authoritative)
    - deadline: org_wins
    """

    DEFAULT_STRATEGIES = {
        ConflictType.STATE: ConflictStrategy.ORG_WINS,
        ConflictType.TITLE: ConflictStrategy.ORG_WINS,
        ConflictType.DESCRIPTION: ConflictStrategy.MERGE,
        ConflictType.PRIORITY: ConflictStrategy.ORG_WINS,
        ConflictType.DEADLINE: ConflictStrategy.ORG_WINS,
        ConflictType.LABELS: ConflictStrategy.MERGE,
        ConflictType.COMMENTS: ConflictStrategy.EXTERNAL_WINS,
    }

    def __init__(self, config: Optional[Dict[str, ConflictStrategy]] = None):
        """
        Initialize resolver with strategy configuration.

        Args:
            config: Dict mapping conflict types to strategies
        """
        self.strategies = {**self.DEFAULT_STRATEGIES}
        if config:
            for key, value in config.items():
                if isinstance(key, str):
                    key = ConflictType(key)
                if isinstance(value, str):
                    value = ConflictStrategy(value)
                self.strategies[key] = value

    def resolve(self, conflict: Conflict) -> ConflictResolution:
        """
        Resolve a conflict using configured strategies.

        Args:
            conflict: The conflict to resolve

        Returns:
            ConflictResolution with changes to apply
        """
        org_changes = {}
        external_changes = {}
        needs_human_review = False
        notes = []

        for field_conflict in conflict.fields:
            strategy = self.strategies.get(
                field_conflict.conflict_type,
                ConflictStrategy.ASK
            )

            if strategy == ConflictStrategy.ORG_WINS:
                # Push org value to external
                external_changes[field_conflict.field_name] = field_conflict.org_value
                notes.append(f"{field_conflict.field_name}: org wins")

            elif strategy == ConflictStrategy.EXTERNAL_WINS:
                # Apply external value to org
                org_changes[field_conflict.field_name] = field_conflict.external_value
                notes.append(f"{field_conflict.field_name}: external wins")

            elif strategy == ConflictStrategy.MERGE:
                # Try to merge
                merged = self._try_merge(field_conflict)
                if merged is not None:
                    org_changes[field_conflict.field_name] = merged
                    external_changes[field_conflict.field_name] = merged
                    notes.append(f"{field_conflict.field_name}: merged")
                else:
                    # Merge failed, needs human review
                    needs_human_review = True
                    notes.append(f"{field_conflict.field_name}: merge failed, needs review")

            elif strategy == ConflictStrategy.ASK:
                needs_human_review = True
                notes.append(f"{field_conflict.field_name}: needs human decision")

        resolution = ConflictResolution(
            conflict=conflict,
            strategy_used=ConflictStrategy.MERGE if len(set(
                self.strategies.get(f.conflict_type) for f in conflict.fields
            )) > 1 else list(set(
                self.strategies.get(f.conflict_type) for f in conflict.fields
            ))[0] if conflict.fields else ConflictStrategy.ORG_WINS,
            org_changes=org_changes,
            external_changes=external_changes,
            needs_human_review=needs_human_review,
            notes="; ".join(notes)
        )

        # Mark conflict as resolved if no human review needed
        if not needs_human_review:
            conflict.resolved = True
            conflict.resolved_at = datetime.now()
            conflict.resolution_strategy = resolution.strategy_used
            conflict.resolved_by = "auto"

        return resolution

    def _try_merge(self, field_conflict: ConflictField) -> Optional[Any]:
        """
        Try to automatically merge conflicting values.

        Returns merged value or None if merge not possible.
        """
        if field_conflict.conflict_type == ConflictType.DESCRIPTION:
            return self._merge_descriptions(
                field_conflict.org_value,
                field_conflict.external_value
            )

        if field_conflict.conflict_type == ConflictType.LABELS:
            return self._merge_labels(
                field_conflict.org_value,
                field_conflict.external_value
            )

        # Other types can't be automatically merged
        return None

    def _merge_descriptions(
        self,
        org_desc: str,
        external_desc: str
    ) -> Optional[str]:
        """
        Try to merge descriptions.

        Simple strategy: if one is a subset of the other, use the longer one.
        Otherwise, concatenate with separator.
        """
        org_normalized = org_desc.strip().lower()
        external_normalized = external_desc.strip().lower()

        # If one contains the other, use the longer
        if org_normalized in external_normalized:
            return external_desc
        if external_normalized in org_normalized:
            return org_desc

        # Concatenate with separator
        return f"{org_desc}\n\n---\n[Merged from external]\n{external_desc}"

    def _merge_labels(
        self,
        org_labels: List[str],
        external_labels: List[str]
    ) -> List[str]:
        """Merge labels by union."""
        org_set = set(org_labels) if org_labels else set()
        external_set = set(external_labels) if external_labels else set()
        return list(org_set | external_set)


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
            data_dir = Path(os.environ.get("DATA_DIR", os.path.expanduser("~/Data")))
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
                }
                for f in conflict.fields
            ],
            "org_task": {
                "id": conflict.org_task.id if conflict.org_task else None,
                "title": conflict.org_task.title if conflict.org_task else None,
                "file_path": conflict.org_task.file_path if conflict.org_task else None,
            } if conflict.org_task else None,
            "external_task": {
                "id": conflict.external_task.id if conflict.external_task else None,
                "title": conflict.external_task.title if conflict.external_task else None,
                "url": conflict.external_task.url if conflict.external_task else None,
            } if conflict.external_task else None,
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
                json.dumps(conflict_data)
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
            )
            for f in data.get("fields", [])
        ]

        return Conflict(
            id=row["id"],
            external_id=row["external_id"],
            org_task_id=row["org_task_id"],
            detected_at=datetime.fromisoformat(row["detected_at"]),
            fields=fields,
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


def load_conflict_config() -> Dict[str, ConflictStrategy]:
    """
    Load conflict resolution config from settings.yaml.

    Returns:
        Dict mapping conflict type names to strategies
    """
    import yaml

    data_dir = Path(os.environ.get("DATA_DIR", os.path.expanduser("~/Data")))

    # Try local settings first, then base settings
    for settings_file in ["settings.local.yaml", "settings.yaml"]:
        settings_path = data_dir / ".datacore" / settings_file
        if settings_path.exists():
            try:
                with open(settings_path) as f:
                    settings = yaml.safe_load(f)

                conflict_config = settings.get("sync", {}).get("conflict_resolution", {})

                # Convert string values to enums
                result = {}
                for key, value in conflict_config.items():
                    try:
                        result[key] = ConflictStrategy(value)
                    except ValueError:
                        pass  # Invalid strategy, skip

                return result
            except Exception:
                pass

    return {}


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
