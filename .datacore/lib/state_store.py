#!/usr/bin/env python3
"""Shared YAML state file utility. Eliminates duplicate _load/_save patterns across lib/."""

import os
import yaml
from pathlib import Path
from typing import Any

from file_utils import atomic_write_yaml, locked_read_modify_write_yaml

def _data_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))

class YamlStateStore:
    """Read/write a YAML state file with auto-mkdir and a configurable default."""

    def __init__(self, relative_path: str, default: Any = None, data_root: Path = None):
        """
        Args:
            relative_path: Path relative to data_root (e.g. ".datacore/state/learning_metrics.yaml")
            default: Value to return when file doesn't exist (deep-copied on load)
            data_root: Override for ~/Data
        """
        root = data_root or _data_root()
        self.path = root / relative_path
        self._default = default if default is not None else {}

    def load(self) -> Any:
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f)
                if data is not None:
                    return data
                return self._default.copy() if isinstance(self._default, dict) else self._default
        return self._default.copy() if isinstance(self._default, dict) else self._default

    def save(self, data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_yaml(self.path, data)

    def append_to_list(self, entries: list, max_size: int = 0) -> None:
        """Load a YAML file as a list, append entries, optionally cap size, and save."""
        def _modifier(existing_data):
            existing = []
            if isinstance(existing_data, list):
                existing = existing_data
            existing.extend(entries)
            if max_size > 0:
                existing = existing[-max_size:]
            return existing
        self.path.parent.mkdir(parents=True, exist_ok=True)
        locked_read_modify_write_yaml(self.path, _modifier)
