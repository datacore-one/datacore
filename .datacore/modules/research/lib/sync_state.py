"""
Sync state management for Readwise integration.
Tracks which documents have been imported to avoid duplicates.
"""

import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional, Set


def _get_state_file(data_root: Path = None) -> Path:
    """Get path to state file."""
    root = data_root or Path.home() / "Data"
    return root / ".datacore" / "state" / "research" / "readwise_sync.yaml"


def _ensure_state_dir(state_file: Path):
    """Ensure state directory exists."""
    state_file.parent.mkdir(parents=True, exist_ok=True)


def load_state(data_root: Path = None) -> dict:
    """Load sync state from file."""
    state_file = _get_state_file(data_root)
    _ensure_state_dir(state_file)

    if state_file.exists():
        with open(state_file) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_state(state: dict, data_root: Path = None):
    """Save sync state to file."""
    state_file = _get_state_file(data_root)
    _ensure_state_dir(state_file)

    with open(state_file, "w") as f:
        yaml.dump(state, f, default_flow_style=False)


def get_last_sync(data_root: Path = None) -> Optional[str]:
    """Get ISO timestamp of last successful sync."""
    return load_state(data_root).get("last_sync")


def set_last_sync(timestamp: str = None, data_root: Path = None):
    """Update last sync timestamp."""
    state = load_state(data_root)
    state["last_sync"] = timestamp or datetime.utcnow().isoformat() + "Z"
    save_state(state, data_root)


def get_imported_ids(data_root: Path = None) -> Set[str]:
    """Get set of document IDs already imported."""
    return set(load_state(data_root).get("imported_ids", []))


def add_imported_id(doc_id: str, data_root: Path = None):
    """Mark a document as imported."""
    state = load_state(data_root)
    imported = set(state.get("imported_ids", []))
    imported.add(doc_id)
    state["imported_ids"] = sorted(list(imported))
    save_state(state, data_root)


def add_imported_ids(doc_ids: list, data_root: Path = None):
    """Mark multiple documents as imported."""
    state = load_state(data_root)
    imported = set(state.get("imported_ids", []))
    imported.update(doc_ids)
    state["imported_ids"] = sorted(list(imported))
    save_state(state, data_root)


def get_sync_stats(data_root: Path = None) -> dict:
    """Get sync statistics."""
    state = load_state(data_root)
    return {
        "last_sync": state.get("last_sync"),
        "total_imported": len(state.get("imported_ids", [])),
        "last_import_count": state.get("last_import_count", 0)
    }


def set_last_import_count(count: int, data_root: Path = None):
    """Record how many items were imported in last sync."""
    state = load_state(data_root)
    state["last_import_count"] = count
    save_state(state, data_root)
