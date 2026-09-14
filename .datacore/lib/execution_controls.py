"""Shared parsing of the operator's cadence pause and global snooze controls.

An absent optional policy uses defaults. An existing invalid policy is an
error, never permission to execute. Both schedulers must interpret the same
snapshot before doing work. Naive historical snooze timestamps mean UTC.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import yaml
from yaml_safety import UniqueStringKeyLoader
from preserved_yaml import validate_tree, load_preserved, dump_preserved
from file_utils import read_text_within, atomic_write_text_within, file_lock, private_state_directory


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


def _location(path):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise PolicyError('policy path is invalid')
    root = path.parent
    while not root.exists() and not root.is_symlink():
        root = root.parent
    if root.is_symlink():
        raise PolicyError('policy directory is aliased')
    boundary = root.resolve(strict=True)
    if boundary != root:
        raise PolicyError('policy ancestor is aliased')
    return boundary, boundary / path.relative_to(root)


def _validate(data):
    validate_tree(data)
    if not isinstance(data, dict):
        raise PolicyError('policy must be a mapping')
    if 'enabled' in data and type(data['enabled']) is not bool:
        raise PolicyError('enabled must be a boolean')
    paused = data.get('paused_cadences', [])
    if not isinstance(paused, list) or any(not isinstance(x, str) or not x.strip() for x in paused):
        raise PolicyError('paused_cadences must be a list of nonempty strings')
    snooze_time(data.get('snoozed_until'))
    for name in ('thresholds', 'engram_recall', 'attention_overrides'):
        if name in data and not isinstance(data[name], dict):
            raise PolicyError('policy section must be a mapping')
    for identity, override in data.get('attention_overrides', {}).items():
        if not identity.strip() or not isinstance(override, dict):
            raise PolicyError('attention override is invalid')
        if 'force_band' in override and override['force_band'] not in ('green', 'yellow', 'red'):
            raise PolicyError('attention override band is invalid')
        if override.get('snoozed_until') not in (None, ''):
            snooze_time(override['snoozed_until'])
    return data


def _parse(source):
    return _validate(yaml.load(source, Loader=_PolicyLoader))


def read_policy(path: Path) -> dict:
    """Only absence selects defaults; invalid or aliased evidence stops work."""
    try:
        root, path = _location(path)
        source = read_text_within(root, path, limit=65536)
        if source is None:
            return {}
        data = _parse(source)
        until = snooze_time(data.get('snoozed_until'))
        if until is not None:
            data['snoozed_until'] = until.isoformat()
        return data
    except (ValueError, OSError, UnicodeError, yaml.YAMLError, RecursionError):
        raise PolicyError('policy is invalid or unreadable; preserve and reconcile it') from None


def update_policy(path: Path, mutate) -> dict:
    """Serialize complete preserved edits; acknowledge only durable publication.

    The lock has a stable private inode independent of the replaced source.
    Other writers must use this transaction; independent manual edits observed
    before publication are held for reconciliation.
    """
    try:
        root, path = _location(path)
        digest = hashlib.sha256(str(path).encode()).hexdigest()
        private = private_state_directory('policy-writes/' + digest)
        with file_lock(private / 'policy', timeout=30):
            before = read_text_within(root, path, limit=65536)
            if before is None:
                document = {}
            else:
                _parse(before)
                document = load_preserved(before)
            mutate(document)
            _validate(document)
            payload = dump_preserved(document)
            if len(payload.encode()) > 65536:
                raise PolicyError('policy exceeds 64 KiB')
            if read_text_within(root, path, limit=65536) != before:
                raise PolicyError('policy changed during mutation')
            atomic_write_text_within(root, path, payload, overwrite=before is not None)
            if read_text_within(root, path, limit=65536) != payload:
                raise PolicyError('policy publication could not be verified')
            return deepcopy(_parse(payload))
    except (ValueError, OSError, UnicodeError, yaml.YAMLError, RecursionError):
        raise PolicyError('policy edit failed; preserve and reconcile it') from None


def globally_paused(policy: dict, now: datetime | None = None) -> tuple[bool, str | None]:
    until = snooze_time(policy.get('snoozed_until'))
    paused = policy.get('enabled', True) is False or (
        until is not None and until > (now or datetime.now(timezone.utc)))
    return paused, until.isoformat() if until else None
