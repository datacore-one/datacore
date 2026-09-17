"""Three-way reconciliation between authored edits, a local base and the ledger.

A generated file is a cache. Its unchanged fields must never be fed back into
newer ledger state. The base is local and disposable, but absence is not proof
that a conflicting file is authoritative: bootstrap requires agreement.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from .genesis import body_text, task_payload
from .edits import EditConflict, merge_values, conditional_payload
from .projector import GENERATED_HEADER, project

STATE = Path('.datacore/state/projection/last-rendered.json')
FIELDS = ('title', 'state', 'scheduled', 'deadline', 'tags', 'effective_tags', 'parent', 'level', 'org', 'created')


ProjectionConflict = EditConflict


_reviewed = ContextVar('reviewed_ledger_roots', default=None)


@contextmanager
def reviewed_state(roots):
    """Bind an explicit review to the ledger version used to plan its edits."""
    token = _reviewed.set(roots)
    try:
        yield
    finally:
        _reviewed.reset(token)


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
    from org_literal import require_resolved_source
    require_resolved_source(text)
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
            # Verification must notice content a renderer would strip -- with
            # one exception, because it is not content: an EMPTY :LOGBOOK:
            # drawer, which Emacs writes by itself on any TODO state change.
            # Left in, it makes the authored file differ from the projection by
            # two lines nobody typed, and the three-way merge has no way to
            # resolve that: one closed task stopped 0-personal's ingest on every
            # cycle (2026-09-16). A logbook with entries in it is data and is
            # compared as before.
            from .projector import strip_empty_logbook
            payload['org']['body'] = strip_empty_logbook(body_text(node).rstrip())
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
    from .log import EventLog, read_events
    from .fold import fold
    expected = _reviewed.get()
    if expected is not None and expected.get(str(Path(space).resolve())) != state.state_root():
        raise ProjectionConflict('reviewed ledger state changed; rebuild the decision board')
    target = Path(space) / 'org/next_actions.org'
    current_text = target.read_text(encoding='utf-8')
    proposed = project(state, space=Path(space).name, as_of=time.time()).text
    # Compare against what would actually be WRITTEN, not the intermediate.
    # project() emits no in-buffer settings; ledger_project_org._with_org_header
    # puts the authored `#+` lines back, and it is that text which lands on
    # disk. Reconciling against the header-less intermediate meant a file
    # carrying `#+FILETAGS: :gtd:` disagreed with the ledger on every item that
    # inherited the tag -- 46 of 0-personal's differences were this alone, and
    # reconciliation had no way to converge.
    try:
        from ledger_project_org import _with_org_header
        proposed = _with_org_header(Path(space), target, proposed, remember=False)
    except Exception:  # noqa: BLE001 - comparing the intermediate is the old behaviour
        pass
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
        appended = []
        for kind, payload in [('item.update', p) for p in updates] + [('item.dismiss', p) for p in dismissals]:
            event = log.append(kind, payload)
            appended.append((payload['id'], event.hash))
            if expected is not None:
                item = fold(read_events(space)).items[payload['id']]
                if event.hash in item.edit_conflicts:
                    raise ProjectionConflict('concurrent ledger edit refused the reviewed decision; reconcile its retained event')
        if appended:
            _advance_base(space, current_text, appended)
    return {'dismissed': len(dismissals), 'updated': len(updates)}


def _advance_base(space, current_text, appended):
    """After the file's changes are IN the ledger, the file is the common ancestor.

    The base (last-rendered.json) used to move only when a projection wrote it.
    So a property written twice between two projections -- which is what every
    nightshift task does to NIGHTSHIFT_ATTEMPT, `pending:` at start and the
    outcome at finish -- met a base still holding the value from BEFORE the
    first write: base absent, file `unknown:`, ledger `pending:`. The merge read
    the run's own first write as someone else's and refused the second:
    "concurrent edit at items.<id>.org.properties.NIGHTSHIFT_ATTEMPT". Every task
    of the 2026-09-17 06:00Z run failed that way, and task 1 of the 16:18Z run.

    Advancing is sound only when the ledger ACCEPTED every change. If any event
    was retained as an edit conflict, the file still holds a value the ledger
    refused, and making it the base would let the next projection overwrite
    that authored edit silently. Then the base stays where it was, and the
    conflict surfaces as it always has.
    """
    from .fold import fold
    from .log import read_events
    if load_base(space) is None:
        return  # no base yet: its first adoption is the projector's decision, not this one's
    items = fold(read_events(space)).items
    if any(event_hash in (items[identity].edit_conflicts or {}) for identity, event_hash in appended):
        return
    path = Path(space) / STATE
    document = base_document(current_text)
    import org_transaction
    if org_transaction._current.get() is not None:
        # Inside the caller's serialized transaction (update_task and
        # sync_state both hold one): a later failure rolls the base back
        # together with the Org write it describes.
        org_transaction.write_org_text(path, document)
        return
    import os
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.last-rendered.')
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(document)
    os.replace(tmp, path)
