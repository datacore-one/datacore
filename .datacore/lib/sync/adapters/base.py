"""
Base adapter interface for external sync.

DIP-0010: External Sync Architecture

The sync infrastructure is payload-agnostic. Different adapters sync
different content types:

- GitHub adapter: OrgTask <-> GitHub Issues
- Calendar adapter: OrgCalendarEntry <-> Google Calendar events
- Asana adapter: OrgTask <-> Asana Tasks

All content lives in org-mode. Adapters handle bidirectional sync.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskState(Enum):
    """Org-mode task states."""
    TODO = "TODO"
    NEXT = "NEXT"
    WAITING = "WAITING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class Priority(Enum):
    """Task priority levels."""
    A = "A"  # High
    B = "B"  # Medium
    C = "C"  # Low
    NONE = None


class ChangeType(Enum):
    """Types of changes detected during sync."""
    CREATED = "created"
    UPDATED = "updated"
    STATE_CHANGED = "state_changed"
    DELETED = "deleted"
    CLOSED = "closed"


# =============================================================================
# Abstract Base: OrgEntry
# =============================================================================

@dataclass
class OrgEntry:
    """
    Abstract base class for any org-mode entry.

    All synced content types inherit from this base, providing
    common fields for identity and sync metadata.
    """
    id: str  # Unique identifier (heading path or generated)
    title: str
    body: str = ""
    file_path: str = ""
    line_number: int = 0
    properties: Dict[str, str] = field(default_factory=dict)

    # Sync metadata
    external_id: Optional[str] = None  # e.g., "github:owner/repo#42", "calendar:primary/abc123"
    external_url: Optional[str] = None
    sync_status: Optional[str] = None  # "synced", "pending", "failed"
    sync_updated: Optional[datetime] = None

    @property
    def is_synced(self) -> bool:
        """Check if entry has external link."""
        return self.external_id is not None


# =============================================================================
# Task-specific: OrgTask (for next_actions.org)
# =============================================================================

@dataclass
class OrgTask(OrgEntry):
    """
    Represents an org-mode task (TODO item).

    Used for syncing with task management tools like GitHub Issues, Asana.
    Lives in next_actions.org.
    """
    state: TaskState = TaskState.TODO
    priority: Optional[Priority] = None
    deadline: Optional[datetime] = None
    scheduled: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)


# =============================================================================
# Calendar-specific: OrgCalendarEntry (for calendar.org)
# =============================================================================

@dataclass
class OrgCalendarEntry(OrgEntry):
    """
    Represents an org-mode calendar entry (event/appointment).

    Used for syncing with calendar services like Google Calendar.
    Lives in calendar.org. Uses timestamps (<...>) not DEADLINE/SCHEDULED.

    Example org entry:
        * Meeting with investors
          :PROPERTIES:
          :EXTERNAL_ID: calendar:primary/abc123
          :END:
          <2025-12-10 Tue 10:00-11:00>
          Discussion topics...
    """
    timestamp: Optional[datetime] = None  # Event start time
    end_time: Optional[datetime] = None   # Event end time (optional)
    repeater: Optional[str] = None        # e.g., "+1w", "+1m"
    location: Optional[str] = None
    attendees: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    @property
    def is_all_day(self) -> bool:
        """Check if event is all-day (no time component)."""
        if self.timestamp is None:
            return True
        return self.timestamp.hour == 0 and self.timestamp.minute == 0 and self.end_time is None

    @property
    def duration_minutes(self) -> Optional[int]:
        """Calculate event duration in minutes."""
        if self.timestamp and self.end_time:
            delta = self.end_time - self.timestamp
            return int(delta.total_seconds() / 60)
        return None


@dataclass
class ExternalTask:
    """Represents a task from an external system."""
    id: str  # External system ID (e.g., issue number)
    title: str
    state: str  # Raw state from external system
    url: str
    created_at: datetime
    updated_at: datetime

    # Optional fields
    body: str = ""
    labels: List[str] = field(default_factory=list)
    assignee: Optional[str] = None
    due_date: Optional[datetime] = None

    # Raw data for adapter-specific handling
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalTaskRef:
    """Reference to an external task."""
    adapter: str  # e.g., "github"
    external_id: str  # Full ID like "github:owner/repo#42"
    url: str

    @classmethod
    def from_external_id(cls, external_id: str, url: str = "") -> "ExternalTaskRef":
        """Create from external_id string like 'github:owner/repo#42'."""
        adapter = external_id.split(":")[0] if ":" in external_id else "unknown"
        return cls(adapter=adapter, external_id=external_id, url=url)


@dataclass
class TaskChange:
    """Represents a change to sync."""
    change_type: ChangeType
    external_task: Optional[ExternalTask] = None
    org_task: Optional[OrgTask] = None
    timestamp: datetime = field(default_factory=datetime.now)

    # For state changes
    old_state: Optional[str] = None
    new_state: Optional[str] = None

    # Change details
    changed_fields: List[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    items_processed: int = 0
    items_created: int = 0
    items_updated: int = 0
    items_failed: int = 0
    errors: List[str] = field(default_factory=list)
    changes: List[TaskChange] = field(default_factory=list)

    def add_error(self, error: str):
        """Add an error message."""
        self.errors.append(error)
        self.items_failed += 1


class TaskSyncAdapter(ABC):
    """
    Abstract base class for task sync adapters.

    Each adapter handles sync with a specific external system
    (GitHub, Asana, Linear, Calendar, etc.)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter name (e.g., 'github', 'asana')."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if adapter is properly configured."""
        pass

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """
        Test connection to external service.

        Returns:
            Tuple of (success, message)
        """
        pass

    @abstractmethod
    def pull_changes(self, since: Optional[datetime] = None) -> List[TaskChange]:
        """
        Fetch changes from external tool since timestamp.

        Args:
            since: Only fetch changes after this time. If None, fetch all.

        Returns:
            List of TaskChange objects representing external changes.
        """
        pass

    @abstractmethod
    def push_changes(self, changes: List[TaskChange]) -> SyncResult:
        """
        Push org-mode changes to external tool.

        Args:
            changes: List of changes to push.

        Returns:
            SyncResult with operation outcome.
        """
        pass

    @abstractmethod
    def create_task(self, task: OrgTask) -> Optional[ExternalTaskRef]:
        """
        Create new task in external tool.

        Args:
            task: Org task to create externally.

        Returns:
            ExternalTaskRef if created, None on failure.
        """
        pass

    @abstractmethod
    def update_task(self, ref: ExternalTaskRef, task: OrgTask) -> bool:
        """
        Update existing task in external tool.

        Args:
            ref: Reference to external task.
            task: Updated org task data.

        Returns:
            True if update successful.
        """
        pass

    @abstractmethod
    def close_task(self, ref: ExternalTaskRef) -> bool:
        """
        Close/complete task in external tool.

        Args:
            ref: Reference to external task.

        Returns:
            True if close successful.
        """
        pass

    @abstractmethod
    def find_matching_task(self, task: OrgTask) -> Optional[ExternalTaskRef]:
        """
        Search for existing external task matching org task.

        Used for duplicate detection when EXTERNAL_ID is missing.

        Args:
            task: Org task to find match for.

        Returns:
            ExternalTaskRef if match found, None otherwise.
        """
        pass

    def map_state_to_external(self, state: TaskState) -> str:
        """
        Map org-mode state to external system state.

        Override in subclasses for system-specific mapping.
        """
        mapping = {
            TaskState.TODO: "open",
            TaskState.NEXT: "open",
            TaskState.WAITING: "open",
            TaskState.DONE: "closed",
            TaskState.CANCELLED: "closed",
        }
        return mapping.get(state, "open")

    def map_state_from_external(self, external_state: str) -> TaskState:
        """
        Map external system state to org-mode state.

        Override in subclasses for system-specific mapping.
        """
        if external_state.lower() in ("closed", "done", "completed", "resolved"):
            return TaskState.DONE
        return TaskState.TODO

    def map_priority_to_external(self, priority: Optional[Priority]) -> Optional[str]:
        """Map org priority to external label/field."""
        mapping = {
            Priority.A: "priority-high",
            Priority.B: "priority-medium",
            Priority.C: "priority-low",
        }
        return mapping.get(priority) if priority else None

    def map_priority_from_external(self, labels: List[str]) -> Optional[Priority]:
        """Map external labels to org priority."""
        for label in labels:
            label_lower = label.lower()
            if "high" in label_lower or label_lower == "p1":
                return Priority.A
            if "medium" in label_lower or label_lower == "p2":
                return Priority.B
            if "low" in label_lower or label_lower == "p3":
                return Priority.C
        return None
