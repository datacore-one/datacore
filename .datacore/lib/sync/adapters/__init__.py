"""
Sync adapters for external task management systems.

DIP-0010: Task Sync Architecture
"""

from .base import (
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

# Adapter registry - populated by adapter imports
_adapters: dict[str, type[TaskSyncAdapter]] = {}


def register_adapter(name: str, adapter_class: type[TaskSyncAdapter]):
    """Register an adapter class."""
    _adapters[name] = adapter_class


def get_adapter(name: str) -> type[TaskSyncAdapter] | None:
    """Get adapter class by name."""
    return _adapters.get(name)


def list_adapters() -> list[str]:
    """List registered adapter names."""
    return list(_adapters.keys())


# Auto-import available adapters
try:
    from .github import GitHubAdapter
    register_adapter("github", GitHubAdapter)
except ImportError:
    pass  # GitHub adapter not available (missing dependencies)

# Future adapters:
# from .calendar import CalendarAdapter
# from .asana import AsanaAdapter
# from .linear import LinearAdapter
