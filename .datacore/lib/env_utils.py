#!/usr/bin/env python3
"""Shared .env file parsing utility. Eliminates duplicate load_env patterns."""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional


def _data_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env file into a dict. Skips comments and blank lines."""
    result = {}
    path = Path(path)
    if not path.exists():
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key:
                    result[key] = val
    return result


def load_env_files(paths: Optional[List[Path]] = None, override: bool = False) -> Dict[str, str]:
    """Load multiple .env files into os.environ. Returns all loaded vars.

    Args:
        paths: List of .env file paths. Defaults to standard Datacore locations.
        override: If True, overwrite existing env vars. Default: only set if not present.
    """
    if paths is None:
        root = _data_root()
        paths = [
            root / ".datacore" / "env" / ".env",
            Path.home() / "config" / "nightshift.env",
        ]

    loaded = {}
    for p in paths:
        parsed = parse_env_file(p)
        for k, v in parsed.items():
            if override or k not in os.environ:
                os.environ[k] = v
            loaded[k] = v
    return loaded
