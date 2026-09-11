"""Three-way reconciliation between authored edits, a local base and the ledger.

A generated file is a cache. Its unchanged fields must never be fed back into
newer ledger state. The base is local and disposable, but absence is not proof
that a conflicting file is authoritative: bootstrap requires agreement.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .genesis import body_text, task_payload
from .edits import EditConflict, merge_values, conditional_payload
from .projector import GENERATED_HEADER, project

STATE = Path('.datacore/state/projection/last-rendered.json')
FIELDS = ('title', 'state', 'scheduled', 'deadline', 'tags', 'effective_tags', 'parent', 'level', 'org', 'created')


ProjectionConflict = EditConflict


def _preamble(text):
    lines = []
    for line in text.splitlines():
        if re.match(r'^\*+ ', line):
            break
        if line in GENERATED_HEADER.splitlines() or line.startswith(('# Generated from ', '#+SEQ_TODO:', '#+TODO:')):
            continue
        if line.strip():
            lines.append(line)
    return lines


def snapshot(text, space):
    """Full editable task fields; reject ambiguous IDs instead of deduplicating."""
    from org_workspace import OrgWorkspace
    from org_workspace._vendor.orgparse import loads
    # Inspect parsed property IDs before OrgWorkspace's duplicate repair.
    # ID-shaped strings in examples/source blocks are ordinary body text.
    ids = [node.get_property('ID') for node in loads(text)[1:] if node.get_property('ID')]
    if any(count > 1 for count in Counter(ids).values()):
        raise ProjectionConflict('duplicate Org IDs; preserve the file and resolve them first')
    with tempfile.TemporaryDirectory(prefix='projection-read-') as directory:
        path = Path(directory) / 'snapshot.org'
        path.write_text(text, encoding='utf-8')
        ws = OrgWorkspace()
        ws.load(path)
        result = {}
        for node in ws.all_nodes():
            identity = node.get_property('ID')
            if not identity:
                raise ProjectionConflict('heading without ID; ingest before projecting')
            payload = task_payload(node, space, '1970-01-01', 'genesis_fallback')
            # Verification must notice content a renderer would strip.
            payload['org']['body'] = body_text(node).rstrip()
            created = node.get_property('CREATED')
            if created:
                created = re.sub(r'^\[(\d{4}-\d{2}-\d{2})(?: [A-Za-z]{3})?\]$', r'\1', str(created))
            payload['created'] = created or None
            result[identity] = {key: payload.get(key) for key in FIELDS}
    return {'items': result, 'preamble': _preamble(text)}


def load_base(space):
    path = Path(space) / STATE
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ProjectionConflict('unreadable projection base; preserve it for recovery') from exc
    if (not isinstance(document, dict) or document.get('version') != 1
            or not isinstance(document.get('text'), str)
            or document.get('sha256') != hashlib.sha256(document['text'].encode()).hexdigest()):
        raise ProjectionConflict('invalid projection base; preserve it for recovery')
    return snapshot(document['text'], Path(space).name)


def base_document(text):
    return json.dumps({'version': 1, 'text': text,
                       'sha256': hashlib.sha256(text.encode()).hexdigest()}, ensure_ascii=False) + '\n'


_changes = merge_values


def reconcile(space, current_text, proposed_text):
    current, remote = snapshot(current_text, Path(space).name), snapshot(proposed_text, Path(space).name)
    base = load_base(space)
    if base is None:
        # Existing local fields must agree before a derived cache can establish
        # its first base. Extra remote items are safe additions.
        for identity, fields in current['items'].items():
            if remote['items'].get(identity) != fields:
                raise ProjectionConflict('no projection base and Org/ledger differ; reconciliation required')
        return remote
    return _changes(base, current, remote)


def guard_projection(space, current_text, proposed_text):
    if current_text is None:
        return
    if Counter(_preamble(current_text)) - Counter(_preamble(proposed_text)):
        raise ProjectionConflict('projection would remove authored preamble content')
    merged = reconcile(space, current_text, proposed_text)
    proposed = snapshot(proposed_text, Path(space).name)
    if merged != proposed:
        raise ProjectionConflict('authored changes are not represented in the ledger; ingest first')


def sync_generated(space, state, actor, dry_run=False):
    """Plan all changes before emitting; stale unchanged projection fields are inert."""
    from .log import EventLog
    current_text = (Path(space) / 'org/next_actions.org').read_text(encoding='utf-8')
    proposed = project(state, space=Path(space).name).text
    merged = reconcile(space, current_text, proposed)
    live = snapshot(proposed, Path(space).name)
    updates, dismissals = [], []
    for identity, fields in merged['items'].items():
        if identity not in live['items']:
            raise ProjectionConflict('new heading is not admitted to the ledger; ingest first')
        existing = live['items'][identity]
        changed = {key: value for key, value in fields.items() if value != existing.get(key)}
        if not changed:
            continue
        if 'created' in changed:
            raise ProjectionConflict('CREATED edit requires explicit ledger provenance reconciliation')
        item = state.items[identity]
        if item.status == 'dismissed':
            raise ProjectionConflict('edit to a terminal item requires explicit reconciliation')
        terminal = changed.get('state') in ('DONE', 'CANCELLED')
        if terminal:
            kind = 'done' if changed.pop('state') == 'DONE' else 'dropped'
        if changed:
            updates.append(conditional_payload(item, changed))
        if terminal:
            expected = deepcopy(item)
            expected.payload.update(deepcopy(changed))
            dismissals.append(conditional_payload(expected, {
                'kind': kind, 'reason': 'authored terminal transition in generated Org'}, terminal=True))
    if set(live['items']) - set(merged['items']):
        raise ProjectionConflict('removed heading needs explicit archive/deletion evidence')
    if not dry_run:
        log = EventLog(space, actor)
        for payload in updates:
            log.append('item.update', payload)
        for payload in dismissals:
            log.append('item.dismiss', payload)
    return {'dismissed': len(dismissals), 'updated': len(updates)}
