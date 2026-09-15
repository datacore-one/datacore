#!/usr/bin/env python3
"""What got finished — the archive and the weekly report.

The projection shows finished work for one day and then drops it, which is
right for an action list and wrong as a permanent answer: a task you completed
should not become indistinguishable from one that never existed. Before this,
it did. `LIVE_STATUSES` omitted completed items entirely, so after the Phase 1
flip finishing something made it silently vanish from the file.

Two operations, and they are different questions:

  archive   append everything closed before the retention window to
            `<space>/4-archive/done/YYYY-MM.org`, once, idempotently. This is
            the durable record — org headings, not JSON, so it is greppable,
            agenda-searchable and readable by the same tools as everything
            else.

  report    what closed in the last N days, per actor, with cost and duration.
            Reads the LEDGER, not the archive, so it is correct even if the
            archive has never been run.

IDEMPOTENCE MATTERS MORE THAN IT LOOKS. This runs on a schedule against an
append-only log, so "archive everything closed before X" will be asked many
times about the same items. Each archive file records the ids it already holds
and skips them, rather than trusting that the last run finished — a half-
written archive that silently duplicates on retry would corrupt the one record
that is supposed to be authoritative.

    ledger_done_report.py archive [--root DIR] [--space NAME] [--days N]
    ledger_done_report.py report  [--root DIR] [--space NAME] [--days N]
"""
from __future__ import annotations

import argparse
import datetime
import os
from copy import deepcopy
from dataclasses import replace
import stat
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from ledger.fold import closure_kind, fold, was_finished  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.projector import render_item, SEQ_TODO  # noqa: E402
from org_transaction import serialized, watch_file, write_org_text  # noqa: E402
from org_workspace._vendor.orgparse import loads  # noqa: E402

CLOSED = ("completed", "verified", "dismissed")


def _default_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _closed_dt(item) -> datetime.datetime | None:
    """Completion time from the HLC's leading millisecond field."""
    raw = getattr(item, "closed_at", None)
    if not raw:
        return None
    try:
        return datetime.datetime.fromtimestamp(float(str(raw).split(".")[0]) / 1000.0,
                                               datetime.timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _spend_by_item(space: Path) -> dict[str, dict]:
    """Cost and model per item, from spend.record events.

    Read separately rather than from `item.complete` because a FAILED run also
    costs money, and the completion event only exists for work that succeeded.
    Totalling only completions would under-report spend by exactly the amount
    spent on things that did not work.
    """
    out: dict[str, dict] = {}
    for ev in read_events(space):
        if ev.type != "spend.record":
            continue
        p = ev.payload or {}
        iid = p.get("item")
        if not iid:
            continue
        row = out.setdefault(iid, {"cents": 0, "model": p.get("model")})
        row["cents"] += int(p.get("cents") or 0)
    return out


def closed_items(space: Path) -> list:
    state = fold(read_events(space))
    return [i for i in state.items.values() if i.status in CLOSED and _closed_dt(i)]


class ArchiveConflict(ValueError):
    """Existing archive content needs reconciliation; never silently replace it."""


def _safe_archive_path(space: Path, target: Path) -> None:
    if space.absolute() != space.resolve() or not space.is_dir():
        raise ArchiveConflict('archive space must be a canonical directory')
    for part in (space / '4-archive', space / '4-archive/done', target):
        if part.is_symlink():
            raise ArchiveConflict('archive path must not contain symbolic links')
        try:
            info = part.stat()
        except FileNotFoundError:
            continue
        if part == target:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ArchiveConflict('archive must be a regular file with one link')
        elif not stat.S_ISDIR(info.st_mode):
            raise ArchiveConflict('archive parent must be a directory')


def _archived_entries(text: str) -> dict[str, str]:
    """Use actual property drawers, never ID-shaped text inside task notes."""
    nodes = list(loads(text))[1:]
    lines = text.splitlines(keepends=True)
    entries = {}
    for index, node in enumerate(nodes):
        identity = node.get_property('ID')
        if not identity:
            continue
        if identity in entries:
            raise ArchiveConflict('duplicate identity in existing archive')
        end = next((later.linenumber - 1 for later in nodes[index + 1:]
                    if later.level <= node.level), len(lines))
        entries[identity] = ''.join(lines[node.linenumber - 1:end]).rstrip()
    return entries


def _archive_entry(item) -> str:
    payload = deepcopy(item.payload or {})
    org = payload['org'] = payload.get('org') or {}
    properties = org['properties'] = org.get('properties') or {}
    # The archive moves each task to a root heading. Preserve tags formerly
    # inherited from its parent/file, just as the projector promotes orphans.
    payload['tags'] = sorted(set(payload.get('effective_tags') or payload.get('tags') or [])
                             | set(payload.get('filetags') or []))
    if item.owner:
        properties['OWNER'] = item.owner
    text = '\n'.join(render_item(replace(item, payload=payload), level=1)) + '\n'
    nodes = list(loads(text))[1:]
    if (len([node for node in nodes if node.level == 1]) != 1
            or set(_archived_entries(text)) != {item.id}
            or nodes[0].get_property('ID') != item.id):
        raise ArchiveConflict('task cannot be archived without changing its identity or structure')
    return text.rstrip()


@serialized
def archive(space: Path, days: int) -> tuple[int, int]:
    """Durably preserve complete terminal tasks, or refuse ambiguous old content."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError('archive retention must be a non-negative integer')
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    stale = [i for i in closed_items(space)
             if i.status in ('verified', 'dismissed')
             and _closed_dt(i) < cutoff and closure_kind(i) != "housekeeping"]
    if not stale:
        return 0, 0

    written = skipped = 0
    by_month: dict[str, list] = {}
    for item in stale:
        by_month.setdefault(_closed_dt(item).strftime("%Y-%m"), []).append(item)

    planned = []
    for month, items in sorted(by_month.items()):
        dest = space / "4-archive" / "done" / f"{month}.org"
        _safe_archive_path(space, dest)
        existing = watch_file(dest)['before'] or ''
        if not existing:
            existing = f"#+TITLE: Completed work — {month}\n{SEQ_TODO}\n"
        have = _archived_entries(existing)

        chunk: list[str] = []
        for item in sorted(items, key=lambda i: (_closed_dt(i), i.id)):
            entry = _archive_entry(item)
            if item.id in have:
                if have[item.id] != entry:
                    raise ArchiveConflict('archived identity has different content; preserve both versions for reconciliation')
                skipped += 1
                continue
            chunk.append(entry)
            have[item.id] = entry
            written += 1
        if chunk:
            planned.append((dest, existing.rstrip('\n') + '\n\n' + '\n\n'.join(chunk) + '\n'))
    # Validate every month before changing any; the transaction also restores
    # earlier replacements when a later write or durability barrier fails.
    for dest, text in planned:
        _safe_archive_path(space, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_org_text(dest, text)
    return written, skipped


def report(space: Path, days: int) -> str:
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    items = [i for i in closed_items(space)
             if _closed_dt(i) >= since and closure_kind(i) != "housekeeping"]
    if not items:
        return f"  {space.name:14} nothing closed in the last {days} day(s)"

    spend = _spend_by_item(space)
    by_actor: dict[str, list] = {}
    for i in items:
        by_actor.setdefault(i.owner or "(unowned)", []).append(i)

    hk = sum(1 for i in closed_items(space)
             if _closed_dt(i) >= since and closure_kind(i) == "housekeeping")
    head = f"  {space.name} — {len(items)} closed in {days} day(s)"
    if hk:
        # Reported separately, never folded in: dedup and id-churn cleanup is
        # not work anybody did.
        head += f"  (+{hk} housekeeping, excluded)"
    lines = [head]
    for actor in sorted(by_actor):
        rows = by_actor[actor]
        done = sum(1 for r in rows if was_finished(r))
        drop = len(rows) - done
        cents = sum(spend.get(r.id, {}).get("cents", 0) for r in rows)
        note = f", {drop} cancelled" if drop else ""
        lines.append(f"    {actor:10} {done} done{note}"
                     + (f" — {cents}c" if cents else ""))
        for r in sorted(rows, key=lambda x: _closed_dt(x))[:5]:
            mark = "✓" if was_finished(r) else "✗"
            lines.append(f"      {mark} {r.title[:66]}")
        if len(rows) > 5:
            lines.append(f"      … and {len(rows) - 5} more")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["archive", "report"])
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--space")
    ap.add_argument("--days", type=int, default=None,
                    help="archive: retention before filing (default 1); "
                         "report: window to report on (default 7)")
    a = ap.parse_args()
    days = a.days if a.days is not None else (1 if a.op == "archive" else 7)

    spaces = [s for s in sorted(a.root.glob("[0-9]-*"))
              if (s / ".datacore" / "events").is_dir()
              and (not a.space or s.name == a.space)]
    if not spaces:
        print(f"ERROR: no spaces with a ledger under {a.root}")
        return 2

    if a.op == "report":
        print(f"DONE — last {days} day(s)\n")
        for s in spaces:
            print(report(s, days))
        return 0

    total = dup = 0
    for s in spaces:
        w, k = archive(s, days)
        total += w
        dup += k
        if w or k:
            print(f"  {s.name:14} archived {w}, already present {k}")
    print(f"\narchived {total} item(s) closed more than {days} day(s) ago"
          + (f"; {dup} already filed" if dup else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
