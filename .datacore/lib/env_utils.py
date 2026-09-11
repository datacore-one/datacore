#!/usr/bin/env python3
"""Shared .env file parsing utility. Eliminates duplicate load_env patterns."""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional


def _data_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))


def parse_env_value(value: str, *, inline_comments: bool = False) -> str:
    """Decode a literal shell-quoted value without expansion or execution."""
    import shlex
    value = value.strip()
    if value.startswith(("'", '"')):
        parts = shlex.split(value, comments=True, posix=True)
        if len(parts) != 1:
            raise ValueError("invalid quoted environment value")
        return parts[0]
    if inline_comments:
        value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
    return value


def parse_env_file(path: Path, *, inline_comments: bool = False) -> Dict[str, str]:
    """Parse literal assignments completely before callers apply any value.

    Blank lines/comments are allowed. Ambiguous, duplicate, malformed or
    unreadable configuration is an error; diagnostics never echo its values.
    inline_comments preserves the CoS contract for unquoted whitespace-#
    comments. The default preserves literal unquoted values for core callers.
    """
    result = {}
    path = Path(path)
    try:
        with path.open('rb') as f:
            raw = f.read(1_048_577)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('environment file symlink is unavailable') from None
        return result
    except OSError:
        raise ValueError('environment file is unreadable') from None
    if len(raw) > 1_048_576:
        raise ValueError('environment file exceeds 1 MiB')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError:
        raise ValueError('environment file is not UTF-8') from None
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, separator, val = line.partition('=')
        key = key.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or key in result:
            raise ValueError(f'invalid or duplicate environment assignment at line {number}')
        try:
            val = parse_env_value(val, inline_comments=inline_comments)
        except ValueError:
            raise ValueError(f'invalid quoted environment value at line {number}') from None
        if '\0' in val:
            raise ValueError(f'invalid environment value at line {number}')
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
            root / ".datacore" / "env" / "local.env",
        ]

    loaded = {}
    pending = {}
    for p in paths:
        parsed = parse_env_file(p)
        for k, v in parsed.items():
            if override or (k not in os.environ and k not in pending):
                pending[k] = v
            loaded[k] = v
    os.environ.update(pending)
    return loaded
