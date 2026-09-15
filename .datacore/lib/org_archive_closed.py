#!/usr/bin/env python3
"""Archive closed level-1/2 subtrees through recoverable Org transactions.

Only DONE/CANCELLED subtrees with no unfinished descendants move. Source and
archive are published together with durable recovery; body bytes and unmoved
lines remain unchanged. Inherited tags are made explicit on the archived root.
"""
import argparse
from datetime import date
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from org_workspace import OrgWorkspace
from org_workspace._vendor.orgparse import loads
from org_transaction import serialized, watch_file, write_org_text

CLOSED = {'DONE', 'CANCELLED'}


def _path(path, *, required=False):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('archive paths must not contain symbolic links')
    try:
        info = path.stat()
    except FileNotFoundError:
        if required:
            raise
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('archive paths must be regular files with one link')
    return path.resolve()


def _parse(text, path):
    workspace = OrgWorkspace()
    return list(loads(text, filename=str(path), env=workspace._parse_env(path)))[1:]


def _spans(text, path):
    lines = text.splitlines(keepends=True)
    nodes = _parse(text, path)
    spans = []
    identities = [node.get_property('ID') for node in nodes if node.get_property('ID')]
    if len(identities) != len(set(identities)):
        raise ValueError('duplicate task identities require reconciliation before archival')
    for index, node in enumerate(nodes):
        start = node.linenumber - 1
        if spans and start < spans[-1][1]:
            continue
        if node.level not in (1, 2) or node.todo not in CLOSED:
            continue
        end_index = next((i for i in range(index + 1, len(nodes)) if nodes[i].level <= node.level), len(nodes))
        descendants = nodes[index + 1:end_index]
        if any(child.todo and child.todo not in CLOSED for child in descendants):
            continue
        end = nodes[end_index].linenumber - 1 if end_index < len(nodes) else len(lines)
        spans.append((start, end, node, descendants))
    return lines, spans


def closed_spans(path):
    """Read-only preview from one byte-preserving snapshot."""
    path = _path(path, required=True)
    lines, spans = _spans(path.read_bytes().decode('utf-8'), path)
    return lines, [(start, end, node.heading, node.level) for start, end, node, _ in spans]


def _archived_lines(lines, start, end, node, descendants):
    chunk = list(lines[start:end])
    if node.level == 1:
        # Shift only parser-recognized headings, never literal star-prefixed
        # text inside a task's body or examples.
        for heading in [node, *descendants]:
            offset = heading.linenumber - 1 - start
            chunk[offset] = '*' + chunk[offset]
    inherited = set(node.tags) - set(node.shallow_tags)
    if inherited:
        first = chunk[0]
        eol = '\r\n' if first.endswith('\r\n') else '\n' if first.endswith('\n') else ''
        title = first[:-len(eol)] if eol else first
        title = re.sub(r'\s+:[^\s:]+(?::[^\s:]+)*:\s*$', '', title)
        chunk[0] = title + ' :' + ':'.join(sorted(node.tags)) + ':' + eol
    return ''.join(chunk)


@serialized
def archive_closed(source, destination, *, dry_run=False):
    source, destination = _path(source, required=True), _path(destination)
    if source == destination:
        raise ValueError('source and archive must be different files')
    # A generated projection must be archived from its authoritative ledger.
    from org_space import ledger_space_for_file
    from ledger_project_org import phase, ORG
    space = ledger_space_for_file(source)
    if space is not None and source == (space / ORG).resolve() and phase(space) == 1:
        raise ValueError('archive generated tasks through ledger_done_report')
    before = watch_file(source)['before']
    lines, spans = _spans(before, source)
    if dry_run or not spans:
        return {'archived': len(spans), 'dry_run': dry_run}
    existing = watch_file(destination)['before'] or ''
    archived_ids = [n.get_property('ID') for n in _parse(existing, destination) if n.get_property('ID')]
    moved_nodes = [n for _, _, node, descendants in spans for n in [node, *descendants]]
    moved_ids = {n.get_property('ID') for n in moved_nodes if n.get_property('ID')}
    if len(archived_ids) != len(set(archived_ids)) or moved_ids.intersection(archived_ids):
        raise ValueError('archive task identity already exists; reconcile before retry')
    today = date.today().isoformat()
    moved = ''.join(_archived_lines(lines, start, end, node, descendants)
                    for start, end, node, descendants in spans)
    keep, cursor = [], 0
    for start, end, _, _ in spans:
        keep.extend(lines[cursor:start]);cursor = end
    keep.extend(lines[cursor:])
    header = f'#+TITLE: Inbox Archive {today}\n\n' if not existing else ''
    separator = '\n' if existing and not existing.endswith('\n') else ''
    archived = existing + separator + header + f'* Archived (processed {today})\n' + moved
    # Validate both final documents before either can change. Recheck link
    # assumptions after parsing; the shared transaction rejects stale bytes.
    _parse(archived, destination)
    _parse(''.join(keep), source)
    _path(source, required=True);_path(destination)
    write_org_text(destination, archived)
    write_org_text(source, ''.join(keep))
    return {'archived': len(spans), 'dry_run': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True)
    parser.add_argument('--archive')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    source = Path(args.file)
    destination = Path(args.archive) if args.archive else source.with_name(f'{source.stem}-archive-{date.today().isoformat()}.org')
    result = archive_closed(source, destination, dry_run=args.dry_run)
    print(f"{result['archived']} closed subtrees {'eligible for archival' if args.dry_run else 'archived'}")


if __name__ == '__main__':
    main()
