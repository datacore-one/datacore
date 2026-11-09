"""Versioned, three-way edit preconditions for replicated item updates.

Legacy updates keep their historical interpretation. New automatic edits carry
an explicit base, so unseen disjoint changes merge and divergent changes remain
visible conflicts. The event log retains every proposed value for recovery.

PROTOCOL VERSIONS (`_merge.version`, owner decision L7, 2026-09-23):

  1  values compare with Python `==`, so `True == 1 == 1.0`: an edit from 1 to
     True reads as "unchanged" and is lost (Lean `Merge.bool_one_edit_lost`).
     Every event written before L7 is version 1 and folds exactly as it did.
  2  values compare TYPE-STRICTLY: two values are equal iff their canonical
     JSON bytes are equal (`ledger.events.canonical_bytes`), so 1, True and 1.0
     are three different values and the three-way laws hold on the nose
     (Lean `Merge.merge_symm_strict`, `merge_base_remote_strict`).

Which version a NEW edit carries is a per-space switch, the file
`.datacore/ledger-edit-protocol`:

  absent    conditional edits are refused (older readers would ignore them)
  1         conditional edits are enabled, version 1
  2         conditional edits are enabled, version 2; `EventLog.append` stamps
            version 2 on every conditional edit it writes

Write `2` only after EVERY reader of the space runs code that understands
version 2: an older reader rejects a version-2 precondition as an invalid edit
and records a conflict, so its state root would diverge from an upgraded one.
"""
from copy import deepcopy
from pathlib import Path

from .events import canonical_bytes


class EditConflict(ValueError):
    pass


PROTOCOL = Path('.datacore/ledger-edit-protocol')

#: Protocol file value -> the `_merge.version` new conditional edits carry.
PROTOCOL_VERSIONS = {'1': 1, '2': 2}


def require_edit_protocol(space):
    """The `_merge.version` this space's conditional edits must carry.

    Do not emit a protocol an older active reader will silently ignore: with no
    (or an unknown) `.datacore/ledger-edit-protocol` value, refuse.
    """
    try:
        value = (Path(space) / PROTOCOL).read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        value = None
    if value not in PROTOCOL_VERSIONS:
        raise EditConflict('conditional edits require ledger-edit-protocol=1 (or 2 for type-strict '
                           'edits) after all active readers are upgraded')
    return PROTOCOL_VERSIONS[value]


def strict_equal(a, b):
    """Type-strict JSON equality: the same canonical bytes (protocol version 2).

    `1`, `True` and `1.0` are three different values; dict key order is not a
    difference. The dict branch's `absent` sentinel equals only itself.
    """
    if a is _ABSENT or b is _ABSENT:
        return a is b
    return canonical_bytes(a) == canonical_bytes(b)


def _loose_equal(a, b):
    return a == b


_ABSENT = object()


def merge_values(base, local, remote, path=(), *, strict=False):
    """Three-way merge; `strict=True` is protocol version 2 (see module doc)."""
    eq = strict_equal if strict else _loose_equal
    if eq(local, base) or eq(local, remote):
        return deepcopy(remote)
    if all(isinstance(value, dict) for value in (base, local, remote)):
        merged = deepcopy(remote)
        absent = _ABSENT
        for key in base.keys() | local.keys():
            before, now, live = base.get(key, absent), local.get(key, absent), remote.get(key, absent)
            if eq(before, now) or eq(now, live):
                continue
            if now is absent:
                if live is not absent and not eq(live, before):
                    raise EditConflict('concurrent edit at ' + '.'.join((*path, key)))
                merged.pop(key, None)
            elif before is absent:
                if live is not absent and not eq(live, now):
                    raise EditConflict('concurrent edit at ' + '.'.join((*path, key)))
                merged[key] = deepcopy(now)
            elif live is absent:
                raise EditConflict('concurrent removal at ' + '.'.join((*path, key)))
            else:
                merged[key] = merge_values(before, now, live, (*path, key), strict=strict)
        return merged
    if not eq(remote, base):
        raise EditConflict('concurrent edit at ' + '.'.join(path))
    return deepcopy(local)


def conditional_payload(item, fields, *, terminal=False, resolves=(), version=1):
    """A conditional edit of `item`.

    `version` defaults to 1; `EventLog.append` re-stamps it to the space's
    protocol version (2 where `.datacore/ledger-edit-protocol` says 2), so
    callers need not know which protocol a space runs.
    """
    if version not in (1, 2) or isinstance(version, bool):
        raise ValueError(f'unknown conditional edit version {version!r}')
    changes = {key: deepcopy(value) for key, value in fields.items() if key != 'id'}
    # Wrapping each field distinguishes absent, null, and an empty dictionary.
    keys = item.payload.keys() if terminal else changes.keys()
    base = {key: {'exists': key in item.payload, 'value': deepcopy(item.payload.get(key))}
            for key in keys}
    condition = {'version': version, 'base': base, 'status': item.status, 'owner': item.owner,
                 'terminal': terminal, 'resolves': list(resolves)}
    return {'id': item.id, **changes, '_merge': condition}


def condition_version(condition):
    """1 or 2 for a well-formed `_merge` block, else None.

    Version 1 keeps its historical test, `version == 1`, which Python also
    passes for `True` and `1.0`; replaying history must not change that.
    Version 2 is only the integer 2.
    """
    if not isinstance(condition, dict):
        return None
    version = condition.get('version')
    if type(version) is int and version == 2:
        return 2
    if version == 1:
        return 1
    return None


def apply_condition(item, payload):
    """Return merged fields without mutating state, or reject the whole event."""
    fields = {key: value for key, value in payload.items() if key not in ('id', '_merge')}
    condition = payload['_merge']
    version = condition_version(condition)
    strict = version == 2
    if (version is None
            or not isinstance(condition.get('base'), dict)
            or not isinstance(condition.get('resolves', []), list)
            or not all(isinstance(key, str) for key in condition.get('resolves', []))
            or not isinstance(condition.get('terminal'), bool)):
        raise EditConflict('invalid conditional edit')
    if condition.get('status') != item.status or condition.get('owner') != item.owner:
        raise EditConflict('item lifecycle changed while editing')
    base = condition['base']
    for entry in base.values():
        if not isinstance(entry, dict) or not isinstance(entry.get('exists'), bool) or 'value' not in entry:
            raise EditConflict('invalid conditional base')
    if condition['terminal']:
        current = {key: {'exists': True, 'value': value} for key, value in item.payload.items()}
        if not (strict_equal(current, base) if strict else current == base):
            raise EditConflict('item content changed before dismissal')
        return deepcopy(fields)
    if set(base) != set(fields):
        raise EditConflict('conditional base must cover exactly the edited fields')
    merged = {}
    for key, value in fields.items():
        desired = {'exists': True, 'value': value}
        current = {'exists': key in item.payload, 'value': item.payload.get(key)}
        result = merge_values(base[key], desired, current, (key,), strict=strict)
        if not result['exists']:
            raise EditConflict('conditional edit cannot remove a top-level field implicitly')
        merged[key] = result['value']
    return merged


def update_payload(item, payload):
    """The complete content an update will retain, for replay and approval.

    Approval binds the resulting content. Edit preconditions are event
    metadata, and must not become a second representation of that content.
    Historical unconditioned updates retain their shallow Org merge.
    """
    if '_merge' in payload:
        fields = apply_condition(item, payload)
        if payload['_merge']['terminal']:
            raise EditConflict('conditional update cannot use a dismissal precondition')
        if item.status == 'dismissed':
            raise EditConflict('conditional edit targets a terminal item')
    else:
        fields = {key: value for key, value in payload.items() if key != 'id'}
    result = deepcopy(item.payload)
    for key, value in fields.items():
        if key == 'org' and isinstance(value, dict) and isinstance(result.get('org'), dict):
            result['org'] = {**result['org'], **deepcopy(value)}
        else:
            result[key] = deepcopy(value)
    return result
