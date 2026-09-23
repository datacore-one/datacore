"""
Datacore Sync Engine

DIP-0010: Task Sync Architecture

Provides bidirectional sync between org-mode and external task systems
(GitHub Issues, Asana, Linear, Calendar, etc.)
"""

from .adapters import (
    TaskSyncAdapter,
    OrgTask,
    ExternalTask,
    ExternalTaskRef,
    TaskChange,
    SyncResult,
    TaskState,
    Priority,
    ChangeType,
)

from .conflict import (
    ConflictType,
    ConflictStrategy,
    ConflictField,
    Conflict,
    ConflictQueue,
    load_conflict_config,
)

__all__ = [
    # Base classes
    "TaskSyncAdapter",
    "OrgTask",
    "ExternalTask",
    "ExternalTaskRef",
    "TaskChange",
    "SyncResult",
    "TaskState",
    "Priority",
    "ChangeType",
    # Conflict queue (Phase 2). The detector/resolver were removed 2026-09-23
    # (decision P7); see sync/conflict.py.
    "ConflictType",
    "ConflictStrategy",
    "ConflictField",
    "Conflict",
    "ConflictQueue",
    "load_conflict_config",
]
