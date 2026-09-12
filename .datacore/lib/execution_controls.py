"""Shared parsing of the operator's cadence pause and global snooze controls.

An absent optional policy uses defaults. An existing invalid policy is an
error, never permission to execute. Both schedulers must interpret the same
snapshot before doing work. Naive historical snooze timestamps mean UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from yaml_safety import UniqueStringKeyLoader


class PolicyError(ValueError):
    """Execution must wait until the operator's policy can be interpreted."""


class _PolicyLoader(UniqueStringKeyLoader):
    error_type = PolicyError
    context = 'policy'


def snooze_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise PolicyError('snoozed_until must be an ISO timestamp or null') from None
    if not isinstance(value, datetime):
        raise PolicyError('snoozed_until must be an ISO timestamp or null')
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def read_policy(path: Path) -> dict:
    """Read a bounded, unambiguous policy without logging its contents."""
    path = Path(path)
    try:
        with path.open('rb') as source:
            raw = source.read(65537)
    except FileNotFoundError:
        if path.is_symlink():
            raise PolicyError('policy symlink target is unavailable') from None
        return {}
    except (OSError, UnicodeError):
        raise PolicyError('policy is unreadable') from None
    if len(raw) > 65536:
        raise PolicyError('policy exceeds 64 KiB')
    try:
        data = yaml.load(raw.decode('utf-8'), Loader=_PolicyLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise PolicyError('policy YAML is invalid') from None
    if not isinstance(data, dict):
        raise PolicyError('policy must be a mapping')
    if 'enabled' in data and type(data['enabled']) is not bool:
        raise PolicyError('enabled must be a boolean')
    paused = data.get('paused_cadences', [])
    if not isinstance(paused, list) or any(not isinstance(x, str) or not x.strip() for x in paused):
        raise PolicyError('paused_cadences must be a list of nonempty strings')
    until = snooze_time(data.get('snoozed_until'))
    if until is not None:
        data['snoozed_until'] = until.isoformat()
    return data


def globally_paused(policy: dict, now: datetime | None = None) -> tuple[bool, str | None]:
    until = snooze_time(policy.get('snoozed_until'))
    paused = policy.get('enabled', True) is False or (
        until is not None and until > (now or datetime.now(timezone.utc)))
    return paused, until.isoformat() if until else None
