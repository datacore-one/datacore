"""
Sync Engine - Orchestrates all sync adapters.

DIP-0010: Task Sync Architecture
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sync.adapters import (
    TaskSyncAdapter,
    OrgTask,
    TaskChange,
    SyncResult,
    ChangeType,
    get_adapter,
    list_adapters,
)


class SyncEngine:
    """
    Orchestrates sync between org-mode and external systems.

    Usage:
        engine = SyncEngine()
        engine.load_config()

        # Pull external changes
        changes = engine.pull_all()

        # Push org changes
        result = engine.push_all(changes)

        # Full sync
        result = engine.sync()
    """

    def __init__(self, data_dir: Optional[str] = None):
        """
        Initialize sync engine.

        Args:
            data_dir: Path to ~/Data. If None, uses DATA_DIR env or ~/Data.
        """
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR", os.path.expanduser("~/Data")))
        self.config_dir = self.data_dir / ".datacore"
        self.adapters: Dict[str, TaskSyncAdapter] = {}
        self.config: Dict[str, Any] = {}
        self._last_sync: Optional[datetime] = None

    def load_config(self) -> bool:
        """
        Load sync configuration from settings.yaml.

        Returns:
            True if config loaded successfully.
        """
        settings_path = self.config_dir / "settings.yaml"
        local_settings_path = self.config_dir / "settings.local.yaml"

        self.config = {}

        # Load base settings
        if settings_path.exists():
            with open(settings_path) as f:
                self.config = yaml.safe_load(f) or {}

        # Merge local settings (overrides)
        if local_settings_path.exists():
            with open(local_settings_path) as f:
                local_config = yaml.safe_load(f) or {}
                self._deep_merge(self.config, local_config)

        # Initialize adapters from config
        self._init_adapters()

        return True

    def _deep_merge(self, base: dict, override: dict):
        """Deep merge override into base dict."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _init_adapters(self):
        """Initialize configured adapters."""
        sync_config = self.config.get("sync", {})
        adapters_config = sync_config.get("adapters", {})

        for adapter_name, adapter_config in adapters_config.items():
            if not adapter_config.get("enabled", False):
                continue

            adapter_class = get_adapter(adapter_name)
            if adapter_class:
                try:
                    self.adapters[adapter_name] = adapter_class(adapter_config)
                except Exception as e:
                    print(f"Warning: Failed to initialize {adapter_name} adapter: {e}")

    def is_enabled(self) -> bool:
        """Check if sync is enabled in config."""
        sync_config = self.config.get("sync", {})
        tasks_config = sync_config.get("tasks", {})
        return tasks_config.get("enabled", False)

    def get_adapter(self, name: str) -> Optional[TaskSyncAdapter]:
        """Get initialized adapter by name."""
        return self.adapters.get(name)

    def list_adapters(self) -> List[str]:
        """List initialized adapter names."""
        return list(self.adapters.keys())

    def pull_all(self, since: Optional[datetime] = None) -> List[TaskChange]:
        """
        Pull changes from all configured adapters.

        Args:
            since: Only fetch changes after this time.

        Returns:
            Combined list of changes from all adapters.
        """
        all_changes = []

        for adapter_name, adapter in self.adapters.items():
            if not adapter.is_configured():
                continue

            try:
                changes = adapter.pull_changes(since)
                all_changes.extend(changes)
            except Exception as e:
                print(f"Error pulling from {adapter_name}: {e}")

        return all_changes

    def push_all(self, changes: List[TaskChange]) -> SyncResult:
        """
        Push changes to all relevant adapters.

        Args:
            changes: List of org-mode changes to push.

        Returns:
            Combined SyncResult.
        """
        result = SyncResult(success=True)

        # Group changes by adapter (based on external_id prefix)
        changes_by_adapter: Dict[str, List[TaskChange]] = {}

        for change in changes:
            if change.org_task and change.org_task.external_id:
                adapter_name = change.org_task.external_id.split(":")[0]
                if adapter_name not in changes_by_adapter:
                    changes_by_adapter[adapter_name] = []
                changes_by_adapter[adapter_name].append(change)
            else:
                # New task - push to default adapter (first one)
                default = list(self.adapters.keys())[0] if self.adapters else None
                if default:
                    if default not in changes_by_adapter:
                        changes_by_adapter[default] = []
                    changes_by_adapter[default].append(change)

        # Push to each adapter
        for adapter_name, adapter_changes in changes_by_adapter.items():
            adapter = self.adapters.get(adapter_name)
            if adapter:
                try:
                    adapter_result = adapter.push_changes(adapter_changes)
                    result.items_processed += adapter_result.items_processed
                    result.items_created += adapter_result.items_created
                    result.items_updated += adapter_result.items_updated
                    result.items_failed += adapter_result.items_failed
                    result.errors.extend(adapter_result.errors)
                except Exception as e:
                    result.add_error(f"{adapter_name}: {str(e)}")

        result.success = result.items_failed == 0
        return result

    def sync(self) -> Dict[str, Any]:
        """
        Perform full bidirectional sync.

        Returns:
            Dict with sync statistics.
        """
        stats = {
            "success": True,
            "pull": {"count": 0, "errors": []},
            "push": {"count": 0, "errors": []},
            "timestamp": datetime.now().isoformat(),
        }

        # Pull external changes
        try:
            changes = self.pull_all(self._last_sync)
            stats["pull"]["count"] = len(changes)
            # TODO: Route changes to org-mode via router
        except Exception as e:
            stats["pull"]["errors"].append(str(e))
            stats["success"] = False

        # TODO: Detect org-mode changes and push
        # This requires comparing org files with last sync state

        self._last_sync = datetime.now()

        return stats

    def diagnostic(self) -> Dict[str, Any]:
        """
        Run sync diagnostic for /diagnostic command.

        Returns:
            Dict with diagnostic information.
        """
        diag = {
            "enabled": self.is_enabled(),
            "adapters": {},
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
        }

        for adapter_name, adapter in self.adapters.items():
            connected, message = adapter.test_connection()
            diag["adapters"][adapter_name] = {
                "configured": adapter.is_configured(),
                "connected": connected,
                "message": message,
            }

            # GitHub-specific diagnostics
            if adapter_name == "github" and hasattr(adapter, "get_open_comments_count"):
                diag["adapters"][adapter_name]["open_comments"] = adapter.get_open_comments_count()

        return diag


# Convenience functions for CLI usage

def sync_diagnostic() -> Dict[str, Any]:
    """Run sync diagnostic (for /diagnostic command)."""
    engine = SyncEngine()
    engine.load_config()
    return engine.diagnostic()


def sync_pull() -> List[TaskChange]:
    """Pull external changes."""
    engine = SyncEngine()
    engine.load_config()
    return engine.pull_all()


def sync_push(changes: List[TaskChange]) -> SyncResult:
    """Push org changes to external."""
    engine = SyncEngine()
    engine.load_config()
    return engine.push_all(changes)


def sync_all() -> Dict[str, Any]:
    """Full bidirectional sync."""
    engine = SyncEngine()
    engine.load_config()
    return engine.sync()


if __name__ == "__main__":
    # CLI usage
    import argparse

    parser = argparse.ArgumentParser(description="Datacore Sync Engine")
    parser.add_argument("command", choices=["diagnostic", "pull", "push", "sync"],
                        help="Command to run")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")

    args = parser.parse_args()

    if args.command == "diagnostic":
        import json
        result = sync_diagnostic()
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "pull":
        changes = sync_pull()
        print(f"Pulled {len(changes)} changes")
        if args.verbose:
            for change in changes:
                print(f"  - {change.change_type.value}: {change.external_task.title if change.external_task else 'N/A'}")

    elif args.command == "sync":
        result = sync_all()
        print(f"Sync complete: {result}")
