#!/usr/bin/env python3
"""Shared YAML state file utility. Eliminates duplicate _load/_save patterns across lib/."""

import os
import yaml
from pathlib import Path
from typing import Any

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
        with open(self.path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    def append_to_list(self, entries: list, max_size: int = 0) -> None:
        """Load a YAML file as a list, append entries, optionally cap size, and save."""
        existing = []
        if self.path.exists():
            try:
                with open(self.path) as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, list):
                        existing = loaded
            except Exception:
                pass
        existing.extend(entries)
        if max_size > 0:
            existing = existing[-max_size:]
        self.save(existing)
