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
]
