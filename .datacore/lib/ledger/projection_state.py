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
from .edits import EditConflict, merge_values, conditional_payload, require_edit_protocol, strict_equal
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


def edit_strict(space):
    """True iff the space runs ledger-edit-protocol 2 (owner follow-up Q3).

    Under protocol 2 every comparison here that decides WHETHER a field
    changed is type-strict -- canonical JSON bytes, the same test
    `edits.merge_values(strict=True)` applies -- so an authored edit from 1 to
    True is proposed rather than read as "unchanged" and lost before it ever
    reaches `conditional_payload`. Under 1, or with no/an unknown protocol
    file, Python `==` is kept and nothing changes.
    """
    try:
        return require_edit_protocol(space) == 2
    except EditConflict:
        return False


def _same(a, b, strict):
    return strict_equal(a, b) if strict else a == b


def changed_fields(fields, existing, *, strict=False):
    """The entries of `fields` whose value differs from `existing`'s."""
    return {key: value for key, value in fields.items() if not _same(value, existing.get(key), strict)}


def reconcile(space, current_text, proposed_text):
    strict = edit_strict(space)
    current, remote = snapshot(current_text, Path(space).name), snapshot(proposed_text, Path(space).name)
    base = load_base(space)
    if base is None:
        # Existing local fields must agree before a derived cache can establish
        # its first base. Extra remote items are safe additions.
        for identity, fields in current['items'].items():
            if not _same(remote['items'].get(identity), fields, strict):
                raise ProjectionConflict('no projection base and Org/ledger differ; reconciliation required')
        return remote
    return _changes(base, current, remote, strict=strict)


def guard_projection(space, current_text, proposed_text):
    if current_text is None:
        return
    if Counter(_preamble(current_text)) - Counter(_preamble(proposed_text)):
        raise ProjectionConflict('projection would remove authored preamble content')
    merged = reconcile(space, current_text, proposed_text)
    proposed = snapshot(proposed_text, Path(space).name)
    if not _same(merged, proposed, edit_strict(space)):
        raise ProjectionConflict('authored changes are not represented in the ledger; ingest first')


def sync_generated(space, state, actor, dry_run=False):
    """Take a view's edits in: one door in (owner decision 8, 2026-10-04).

    With a projection base and no reviewed decision in flight, every edit to
    the view is handled PER TASK and nothing stops the space: a plain change is
    applied to the ledger now, an unclear one is held as one inbox entry
    (`view_capture`), and the base advances so the view is regenerated and the
    same edit is never taken twice. See `_sync_views`.

    The strict path below (`_sync_strict`) is kept for the two callers that
    rely on its refusals: a reviewed decision board (`reviewed_state`), whose
    plan must not be re-interpreted, and a space with no base yet, whose first
    projection requires agreement.
    """
    if _reviewed.get() is None and load_base(space) is not None:
        return _sync_views(space, state, actor, dry_run)
    return _sync_strict(space, state, actor, dry_run)


def _block_text(text, identity):
    """The heading whose own drawer carries `:ID: identity`, with its subtree."""
    lines = text.split('\n')
    for i, line in enumerate(lines):
        m = re.match(r'^(\*+) ', line)
        if not m:
            continue
        level, j, own, found = len(m.group(1)), i + 1, True, False
        while j < len(lines):
            mm = re.match(r'^(\*+) ', lines[j])
            if mm and len(mm.group(1)) <= level:
                break
            if mm:
                own = False
            if own and re.match(rf'^\s*:ID:\s*{re.escape(identity)}\s*$', lines[j]):
                found = True
            j += 1
        if found:
            return '\n'.join(lines[i:j])
    return ''


#: More headings than this gone from a view at once is one entry, not many.
_REMOVED_ONE_ENTRY = 5


def _sync_views(space, state, actor, dry_run=False):
    """Plain edits applied, unclear ones held once, the base advanced. Never raises for an edit."""
    from .log import EventLog, read_events
    from .fold import fold
    from .edits import EditConflict
    from .view_capture import Held, hold, archived_in
    space = Path(space)
    name = space.name
    target = space / 'org/next_actions.org'
    view = target.name
    current_text = target.read_text(encoding='utf-8')
    proposed = project(state, space=name, as_of=time.time()).text
    try:
        from ledger_project_org import _with_org_header
        proposed = _with_org_header(space, target, proposed, remember=False)
    except Exception:  # noqa: BLE001 - comparing the intermediate is the old behaviour
        pass
    strict = edit_strict(space)
    base_doc = json.loads((space / STATE).read_text(encoding='utf-8'))['text']
    current = snapshot(current_text, name)['items']
    base = snapshot(base_doc, name)['items']
    live = snapshot(proposed, name)['items']

    held, plans, archives = [], [], []

    def title_of(identity, fields=None):
        item = state.items.get(identity)
        return (fields or {}).get('title') or (item.title if item else '') or identity

    for identity, fields in current.items():
        item = state.items.get(identity)
        if item is None:
            held.append(Held(identity, 'a heading the ledger could not admit', title_of(identity, fields),
                             _block_text(current_text, identity), view))
            continue
        before = base.get(identity) or live.get(identity)
        local = changed_fields(fields, before or {}, strict=strict)
        if not local:
            continue
        if 'created' in local:
            held.append(Held(identity, 'its CREATED stamp was edited', title_of(identity, fields),
                             _block_text(current_text, identity), view))
            continue
        remote = live.get(identity)
        if remote is not None and identity in base:
            try:
                merged = merge_values(base[identity], fields, remote, strict=strict)
            except EditConflict as exc:
                held.append(Held(identity, f'clash with an edit already in the ledger ({exc})',
                                 title_of(identity, fields), _block_text(current_text, identity), view))
                continue
            changed = changed_fields(merged, remote, strict=strict)
        else:
            changed = changed_fields(fields, remote or {}, strict=strict)
        if changed:
            plans.append((identity, dict(changed), fields.get('state')))

    for identity in base:
        if identity in current:
            continue
        item = state.items.get(identity)
        if item is None or item.status == 'archived' or (item.status == 'dismissed' and identity not in live):
            continue
        if item.status != 'dismissed' and archived_in(space, identity):
            archives.append(identity)
        else:
            held.append(Held(identity, 'heading removed from the view', title_of(identity, base[identity]),
                             _block_text(base_doc, identity), view))
    removed = [h for h in held if h.change == 'heading removed from the view']
    if len(removed) > _REMOVED_ONE_ENTRY:
        held = [h for h in held if h not in removed]
        held.append(Held(removed[0].of, f'{len(removed)} headings removed from the view at once',
                         f'{len(removed)} headings', '\n\n'.join(h.text for h in removed), view))

    before_order = [i for i in base if i in current]
    after_order = [i for i in current if i in base]
    if before_order != after_order:
        k = next(n for n, (x, y) in enumerate(zip(before_order, after_order)) if x != y)
        moved = after_order[k]
        held.append(Held(moved, 'headings reordered by hand (the ledger keeps no order)', title_of(moved),
                         '\n'.join(f'{n + 1}. {title_of(i, current.get(i))}' for n, i in enumerate(after_order)), view))

    summary = {'dismissed': 0, 'updated': 0, 'reopened': 0, 'archived': 0, 'held': len(held)}
    if dry_run:
        summary.update(updated=len(plans), archived=len(archives))
        return {k: v for k, v in summary.items() if k in ('dismissed', 'updated') or v}
    log = EventLog(space, actor)
    version = 2 if strict else 1
    for identity, changed, file_state in plans:
        item = fold(read_events(space)).items[identity]
        closed = item.status in ('dismissed', 'archived')
        file_closed = file_state in ('DONE', 'CANCELLED')
        kind = None
        if closed and not file_closed:
            log.append('item.reopen', {'id': identity, 'reason': f'reopened in {view}'})
            summary['reopened'] += 1
        elif closed:
            # A note, a date or a retitle on a closed task: applied, and the task stays
            # closed. Reopen, edit, close again with the same outcome; nothing is lost.
            kind = 'done' if file_state == 'DONE' else 'dropped'
            if item.status == 'dismissed' and 'state' not in changed:
                kind = item.closed_kind or kind
            changed.pop('state', None)
            log.append('item.reopen', {'id': identity, 'reason': f'annotated after closing, in {view}'})
        elif changed.get('state') in ('DONE', 'CANCELLED'):
            kind = 'done' if changed.pop('state') == 'DONE' else 'dropped'
        if changed:
            item = fold(read_events(space)).items[identity]
            log.append('item.update', conditional_payload(item, changed, version=version))
            summary['updated'] += 1
        if kind:
            item = fold(read_events(space)).items[identity]
            log.append('item.dismiss', conditional_payload(item, {
                'kind': kind, 'reason': f'authored terminal transition in {view}'}, terminal=True, version=version))
            summary['dismissed'] += 1
    for identity in archives:
        log.append('item.archive', {'id': identity, 'reason': f'archived out of {view} (found in an org archive file)'})
        summary['archived'] += 1
    hold(space, held)
    _write_base(space, current_text)
    # The strict path's contract is {'dismissed', 'updated'}; the new counters are
    # reported only when they happened.
    return {k: v for k, v in summary.items() if k in ('dismissed', 'updated') or v}


def _write_base(space, text):
    """Everything in `text` is now in the ledger or held in the inbox: it is the base."""
    path = Path(space) / STATE
    document = base_document(text)
    import org_transaction
    if org_transaction._current.get() is not None:
        org_transaction.write_org_text(path, document)
        return
    import os
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.last-rendered.')
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(document)
    os.replace(tmp, path)


def _sync_strict(space, state, actor, dry_run=False):
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
    strict = edit_strict(space)
    updates, dismissals = [], []
    for identity, fields in merged['items'].items():
        if identity not in live['items']:
            raise ProjectionConflict('new heading is not admitted to the ledger; ingest first')
        existing = live['items'][identity]
        changed = changed_fields(fields, existing, strict=strict)
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
            updates.append(conditional_payload(item, changed, version=2 if strict else 1))
        if terminal:
            # The dismissal's precondition is READ after the update lands, not
            # predicted from it. A dismissal pins the item's ENTIRE payload, and
            # `item.payload + changed` is only a guess at what the update will
            # produce -- `update_payload` merges the `org` sub-dict key by key
            # rather than replacing it, so the guess is wrong whenever an org
            # field the update does not mention is already present. The refusal
            # then reads "item content changed before dismissal" about a change
            # this same call had just made: nightshift 2026-09-17 20:40Z, where
            # the retained conflict stopped 5-plur projecting at all.
            dismissals.append((identity, kind))
    if set(live['items']) - set(merged['items']):
        raise ProjectionConflict('removed heading needs explicit archive/deletion evidence')
    if not dry_run:
        log = EventLog(space, actor)
        appended = []

        def _emit(kind, payload):
            event = log.append(kind, payload)
            appended.append((payload['id'], event.hash))
            if expected is not None:
                item = fold(read_events(space)).items[payload['id']]
                if event.hash in item.edit_conflicts:
                    raise ProjectionConflict('concurrent ledger edit refused the reviewed decision; reconcile its retained event')

        for payload in updates:
            _emit('item.update', payload)
        for identity, kind in dismissals:
            current_item = fold(read_events(space)).items[identity]
            _emit('item.dismiss', conditional_payload(current_item, {
                'kind': kind, 'reason': 'authored terminal transition in generated Org'}, terminal=True,
                version=2 if strict else 1))
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
