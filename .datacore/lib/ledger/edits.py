"""Versioned, three-way edit preconditions for replicated item updates.

Legacy updates keep their historical interpretation. New automatic edits carry
an explicit base, so unseen disjoint changes merge and divergent changes remain
visible conflicts. The event log retains every proposed value for recovery.
"""
from copy import deepcopy
from pathlib import Path


class EditConflict(ValueError):
    pass


PROTOCOL = Path('.datacore/ledger-edit-protocol')


def require_edit_protocol(space):
    """Do not emit a protocol an older active reader will silently ignore."""
    try:
        enabled = (Path(space) / PROTOCOL).read_text(encoding='utf-8').strip() == '1'
    except FileNotFoundError:
        enabled = False
    if not enabled:
        raise EditConflict('conditional edits require ledger-edit-protocol=1 after all active readers are upgraded')


def merge_values(base, local, remote, path=()):
    if local == base or local == remote:
        return deepcopy(remote)
    if all(isinstance(value, dict) for value in (base, local, remote)):
        merged = deepcopy(remote)
        absent = object()
        for key in base.keys() | local.keys():
            before, now, live = base.get(key, absent), local.get(key, absent), remote.get(key, absent)
            if before == now or now == live:
                continue
            if now is absent:
                if live is not absent and live != before:
                    raise EditConflict('concurrent edit at ' + '.'.join((*path, key)))
                merged.pop(key, None)
            elif before is absent:
                if live is not absent and live != now:
                    raise EditConflict('concurrent edit at ' + '.'.join((*path, key)))
                merged[key] = deepcopy(now)
            elif live is absent:
                raise EditConflict('concurrent removal at ' + '.'.join((*path, key)))
            else:
                merged[key] = merge_values(before, now, live, (*path, key))
        return merged
    if remote != base:
        raise EditConflict('concurrent edit at ' + '.'.join(path))
    return deepcopy(local)


def conditional_payload(item, fields, *, terminal=False, resolves=()):
    changes = {key: deepcopy(value) for key, value in fields.items() if key != 'id'}
    # Wrapping each field distinguishes absent, null, and an empty dictionary.
    keys = item.payload.keys() if terminal else changes.keys()
    base = {key: {'exists': key in item.payload, 'value': deepcopy(item.payload.get(key))}
            for key in keys}
    condition = {'version': 1, 'base': base, 'status': item.status, 'owner': item.owner,
                 'terminal': terminal, 'resolves': list(resolves)}
    return {'id': item.id, **changes, '_merge': condition}


def apply_condition(item, payload):
    """Return merged fields without mutating state, or reject the whole event."""
    fields = {key: value for key, value in payload.items() if key not in ('id', '_merge')}
    condition = payload['_merge']
    if (not isinstance(condition, dict) or condition.get('version') != 1
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
        if current != base:
            raise EditConflict('item content changed before dismissal')
        return deepcopy(fields)
    if set(base) != set(fields):
        raise EditConflict('conditional base must cover exactly the edited fields')
    merged = {}
    for key, value in fields.items():
        desired = {'exists': True, 'value': value}
        current = {'exists': key in item.payload, 'value': item.payload.get(key)}
        result = merge_values(base[key], desired, current, (key,))
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
