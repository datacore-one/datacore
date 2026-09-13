"""Bounded, attributed crew diagnostics for the Firm Status panel.

These reports describe observed status. They are not execution attestations or
an OS identity boundary. Publication failures are explicit; callers may continue
independent work but must report that the diagnostic could not be published.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from actor_identity import this_actor
from file_utils import atomic_write_text_within, file_lock, private_state_directory, read_text_within

_AGENT_STATE_DIR = Path(os.environ.get('DATACORE_AGENT_STATE_DIR',
                                      str(Path.home() / 'Data/.datacore/state/agents')))
_VALID_STATUSES = {'ok', 'blocked', 'error'}
_LIMIT = 64 * 1024


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _identity(slug):
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,127}', slug):
        raise ValueError('invalid report identity')
    return slug


def _location(slug, data_dir):
    _identity(slug)
    if data_dir is not None:
        root = Path(data_dir).resolve(strict=True)
        return root, root / '.datacore/state/agents' / (slug + '.json')
    directory = _AGENT_STATE_DIR
    if not directory.is_absolute() or '..' in directory.parts:
        raise ValueError('report directory must be an absolute scoped path')
    if '.datacore' in directory.parts:
        lexical = Path(*directory.parts[:directory.parts.index('.datacore')])
    else:
        lexical = directory
        while not lexical.exists() and not lexical.is_symlink():
            lexical = lexical.parent
    if lexical.is_symlink():
        raise ValueError('report directory is aliased')
    root = lexical.resolve(strict=True)
    return root, root / directory.relative_to(lexical) / (slug + '.json')


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError('invalid report timestamp')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid report timestamp') from None
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError('report timestamp must be aware and not in the future')
    return stamp


def _validate(payload, slug):
    nodes = 0
    active = set()
    def visit(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 10000:
            raise ValueError('report exceeds structural limits')
        if isinstance(value, (list, dict)):
            identity = id(value)
            if identity in active:
                raise ValueError('report contains a cycle')
            active.add(identity)
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError('report keys must be strings')
                children = value.values()
            else:
                children = value
            for child in children:
                visit(child, depth + 1)
            active.remove(identity)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('report numbers must be finite')
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError('report value type is unsupported')
    visit(payload)
    if not isinstance(payload, dict) or payload.get('last_status') not in _VALID_STATUSES:
        raise ValueError('invalid report status')
    for key, limit in [('name', 120), ('last_summary', 300), ('last_error', 300)]:
        value = payload.get(key)
        if key == 'last_error' and value is None:
            continue
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError('invalid report text')
        if key == 'name' and (not value.strip() or len(value.splitlines()) != 1):
            raise ValueError('invalid report display name')
    if payload['last_status'] == 'ok' and payload.get('last_error') is not None:
        raise ValueError('successful report cannot retain an error')
    _stamp(payload.get('last_activity'))
    if 'writer' in payload and payload['writer'] != slug:
        raise ValueError('report writer does not match its identity')
    return payload


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate report field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('nonfinite report number')


def _read(root, path, slug):
    source = read_text_within(root, path, limit=_LIMIT)
    if source is None:
        return None
    try:
        return _validate(json.loads(source, object_pairs_hook=_unique,
                                    parse_constant=_invalid_constant), slug)
    except (ValueError, TypeError, RecursionError):
        raise ValueError('Invalid crew report; preserve and reconcile it') from None


def write_self_report(slug, name, status='ok', summary='', error=None, extra=None, *, data_dir=None):
    """Durably publish the declared actor's diagnostic, preserving valid extras."""
    _identity(slug)
    if slug != this_actor(strict=True):
        raise ValueError('a self-report must use the declared actor')
    if status not in _VALID_STATUSES:
        raise ValueError('invalid report status')
    if extra is not None and not isinstance(extra, dict):
        raise ValueError('report extension must be a mapping')
    reserved = {'name', 'writer', 'last_activity', 'last_status', 'last_error', 'last_summary'}
    if extra and (reserved.intersection(extra) or any(not isinstance(k, str) for k in extra)):
        raise ValueError('report extensions cannot redefine provenance or status')
    root, target = _location(slug, data_dir)
    identity = hashlib.sha256(str(target).encode()).hexdigest()
    directory = private_state_directory('agent-reports/' + identity, data_root=root)
    with file_lock(directory / 'store', timeout=30):
        previous = _read(root, target, slug)
        payload = {**(previous or {}), **(extra or {}),
                   'name': name, 'writer': slug, 'last_activity': _now_iso(),
                   'last_status': status, 'last_error': error, 'last_summary': summary}
        _validate(payload, slug)
        if previous and _stamp(payload['last_activity']) < _stamp(previous['last_activity']):
            raise ValueError('report clock moved backwards')
        if previous and _stamp(payload['last_activity']) == _stamp(previous['last_activity']) and payload != previous:
            raise ValueError('different reports have an ambiguous timestamp')
        try:
            source = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
        except (TypeError, ValueError, RecursionError):
            raise ValueError('report extension cannot be serialized safely') from None
        if len(source.encode()) > _LIMIT:
            raise ValueError('report exceeds size limit')
        atomic_write_text_within(root, target, source, overwrite=previous is not None)
        if _read(root, target, slug) != payload:
            raise ValueError('crew report publication could not be verified')
    return target


def read_self_report(slug, *, data_dir=None):
    """Only actual absence returns None; invalid/unavailable reports remain errors."""
    root, target = _location(slug, data_dir)
    return _read(root, target, slug)
