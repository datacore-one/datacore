#!/usr/bin/env python3
"""Remove duplicate subtrees WITHIN one org file, keeping the first of each.

Why this exists, and why it is not `dedup_tasks.py`. That tool scans
`next_actions.org` and picks the richest of a duplicate group by property count.
This handles a different failure: a single file that has the same entry several
times over because something appended a block more than once. On 2026-08-15 one
commit took `0-personal/org/inbox.org` from 4,102 to 4,218 lines and introduced
four copies each of four health tasks — the "silently corrupted inbox.org" mode
the file itself carries a warning about.

`inbox_dedup.py` is also not this: it removes entries already routed OUT to
another file. Here both copies are in the same file and neither has been routed.

IT REFUSES TO GUESS. A duplicate is removed only when its ENTIRE subtree —
heading, body, properties, logbook, children — is byte-identical to the copy
being kept, EXCEPT for the per-write identifier lines (:ID:, :DISPATCH_ID:,
see GENERATED_PROPS). That exception is deliberate and is the whole point on
the incident below, but it means a dropped copy can carry an :ID: the event
ledger already knows as a separate item. The report names every dropped id
that the kept copy does not carry, and under --apply each such id that the
space's ledger created is dismissed there as `kind=housekeeping`, with a
reason naming the kept id (decision G9, 2026-09-23; see
`ledger_dismiss_housekeeping`). Same-heading entries whose bodies differ are reported and left
alone: one of them may carry notes the other does not, and there is no way to
tell which from the text. Deleting the wrong one loses work silently, and this
runs against a capture point the owner treats as sacred.

SUBTREE, NOT LINE. A block is its heading plus everything up to the next
heading at the same or shallower level, so removing a parent removes its
children with it and never orphans them under an unrelated heading.

    org_dedup_within_file.py FILE...          # report; writes nothing
    org_dedup_within_file.py FILE... --apply  # rewrite, backup alongside

Dry run by default, per the convention of every destructive tool in this repo.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import org_transaction

HEADING = re.compile(r"^(\*+)\s+(.*)$")

# Properties that are MINTED PER WRITE, so two copies of one task never agree on
# them and a byte comparison always reports "bodies differ". Ignoring them is
# what lets the identity test see through the duplication: on 2026-08-15 four
# copies of each health task were identical apart from these two lines.
#
# CREATED is deliberately NOT here. A differing CREATED means the entries were
# captured on different days, which can be a genuine re-capture with refined
# wording — exactly the case that sits next to the duplicates in inbox.org, one
# day later with a specific model number and a better protocol. Keeping CREATED
# significant is what preserves it.
GENERATED_PROPS = ("ID", "DISPATCH_ID")
_GEN_RE = re.compile(r"^\s*:(?:%s):\s" % "|".join(GENERATED_PROPS))


def split_blocks(lines: list[str]) -> tuple[list[str], list[tuple[int, str, list[str]]]]:
    """Return (preamble, [(level, heading_line, block_lines)]).

    block_lines includes the heading itself, so a block is a self-contained
    slice of the file and reassembly is a plain concatenation.
    """
    first = next((i for i, l in enumerate(lines) if HEADING.match(l)), len(lines))
    preamble, rest = lines[:first], lines[first:]

    blocks: list[tuple[int, str, list[str]]] = []
    cur: list[str] | None = None
    cur_level = 0
    for line in rest:
        m = HEADING.match(line)
        if m and (cur is None or len(m.group(1)) <= cur_level or True):
            # Every heading starts a new block; nesting is handled at removal
            # time by consuming deeper-level blocks along with their parent.
            if cur is not None:
                blocks.append((cur_level, cur[0], cur))
            cur = [line]
            cur_level = len(m.group(1))
        else:
            if cur is None:  # pragma: no cover - preamble already split off
                continue
            cur.append(line)
    if cur is not None:
        blocks.append((cur_level, cur[0], cur))
    return preamble, blocks


def with_children(blocks: list[tuple[int, str, list[str]]], i: int) -> tuple[list[str], int]:
    """The full subtree text at index i, and the index just past it."""
    level = blocks[i][0]
    out = list(blocks[i][2])
    j = i + 1
    while j < len(blocks) and blocks[j][0] > level:
        out.extend(blocks[j][2])
        j += 1
    return out, j


def norm_heading(line: str) -> str:
    """Heading identity: level + text, with trailing tags stripped.

    Tags are dropped because a duplicated block sometimes gains or loses a tag
    in transit, and level is kept because `* TODO X` and `** TODO X` are
    genuinely different entries in different places in the outline.
    """
    m = HEADING.match(line)
    if not m:
        return line.strip()
    text = re.sub(r"\s+:[\w:@#%-]+:\s*$", "", m.group(2)).strip()
    return f"{len(m.group(1))}|{text}"


def identity(text: list[str]) -> list[str]:
    """The comparable form of a subtree: content minus per-write identifiers."""
    return [l for l in text if not _GEN_RE.match(l)]


_ID_VALUE = re.compile(r"^\s*:(?:%s):\s+(\S+)" % "|".join(GENERATED_PROPS))


def _ids(text: list[str]) -> list[str]:
    """The per-write identifiers a subtree carries, in order."""
    return [m.group(1) for l in text if (m := _ID_VALUE.match(l))]


def ledger_dismiss_housekeeping(path: Path, dropped_id: str, kept_id: str,
                                tool: str) -> str:
    """Dismiss `dropped_id` in the ledger of `path`'s space; return a status.

    Used when a repair tool removes an :ID: from an org file in favour of
    `kept_id` (G9 here, G10 in org_resolve_id_conflicts). It appends
    `item.dismiss` with `kind=housekeeping` through the adapter's own emit
    helper, `org_workspace_adapter._ledger_emit`, so it follows the same Phase
    0/1 rules as every adapter write. It does nothing, and says why, when:

    * the file is not in a space with a ledger (`.datacore/events`);
    * the ledger never created the id (a dismiss would be an orphan event);
    * the item is already dismissed (dismissal is terminal);
    * the file is a Phase 1 generated projection: there `_ledger_emit`
      reconciles by diff, and a removed heading is refused as a projection
      conflict. `dedup` refuses to rewrite such a file for that reason.
    """
    from org_space import ledger_space_for_file
    space = ledger_space_for_file(path)
    if space is None:
        return "no ledger in this space"
    from ledger.fold import fold
    from ledger.log import read_events
    item = fold(read_events(space)).items.get(dropped_id)
    if item is None:
        return "not in the ledger"
    if item.status == "dismissed":
        return "already dismissed in the ledger"
    from org_workspace_adapter import _generated_target, _ledger_emit
    if _generated_target(path):
        return "NOT dismissed: generated projection, reconcile in the ledger"
    actor = _ledger_emit(path, "item.dismiss", {
        "id": dropped_id, "kind": "housekeeping",
        "reason": f"duplicate of {kept_id}; id dropped by {tool}"})
    return f"dismissed (housekeeping) as {actor}" if actor else "NOT dismissed: ledger append failed"


def _is_generated(path: Path) -> bool:
    try:
        from org_workspace_adapter import _generated_target
        return _generated_target(path)
    except Exception:  # noqa: BLE001 -- an unreadable marker is not "generated"
        return False


def dedup(path: Path, apply: bool) -> tuple[int, int, int]:
    """Returns (removed_blocks, removed_lines, skipped_groups).

    UNDER THE ORG LOCK WHEN APPLYING (owner decision Q12, 2026-09-23). With
    `apply`, the read, the decision and the write happen inside one
    `org_transaction.serialized` call: the file is watched before it is read
    and rewritten with `write_org_text` (atomic, journalled), so an adapter
    commit can neither land between the read and the write nor be overwritten
    by a stale copy. A dry run takes no lock.
    """
    if apply:
        return org_transaction.serialized(_dedup)(path, True)
    return _dedup(path, False)


def _dedup(path: Path, apply: bool) -> tuple[int, int, int]:
    if apply:
        org_transaction.watch_file(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    preamble, blocks = split_blocks(lines)

    # Collapse to top-level-walkable subtrees so a parent carries its children.
    subtrees: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(blocks):
        text, nxt = with_children(blocks, i)
        subtrees.append((norm_heading(blocks[i][1]), text))
        i = nxt

    seen: dict[str, list[str]] = {}  # key -> identity() of the kept copy
    kept_ids: dict[str, list[str]] = {}  # key -> generated ids of the kept copy
    kept: list[list[str]] = []
    removed = removed_lines = skipped = 0
    reports: list[str] = []
    lost_ids: list[tuple[str, str]] = []  # (dropped id, kept id) for the ledger

    for key, text in subtrees:
        if key not in seen:
            seen[key] = identity(text)
            kept_ids[key] = _ids(text)
            kept.append(text)
            continue
        if identity(text) == seen[key]:
            removed += 1
            removed_lines += len(text)
            lost = [i for i in _ids(text) if i not in kept_ids[key]]
            kept_id = kept_ids[key][0] if kept_ids[key] else "an unidentified copy"
            lost_ids.extend((i, kept_id) for i in lost)
            reports.append(f"    drop  {text[0].strip()[:72]}"
                           + (f"  (drops id(s) {', '.join(lost)}; kept copy has "
                              f"{', '.join(kept_ids[key]) or 'none'})" if lost else ""))
            continue
        # Same heading, different content — the one case where deleting either
        # copy could destroy notes. Report and keep both.
        skipped += 1
        kept.append(text)
        reports.append(f"    KEPT BOTH (bodies differ) {text[0].strip()[:52]}")

    if not removed and not skipped:
        return 0, 0, 0

    print(f"  {path}")
    for r in reports:
        print(r)

    if apply and removed and lost_ids and _is_generated(path):
        print("    REFUSED: this file is generated from the ledger (Phase 1); "
              "dismiss the duplicate item in the ledger instead")
        return 0, 0, skipped

    if apply and removed:
        backup = path.with_suffix(path.suffix + ".bak-dedup")
        shutil.copy2(path, backup)
        out = preamble + [l for t in kept for l in t]
        org_transaction.write_org_text(path, "\n".join(out) + "\n")
        print(f"    wrote {len(out)} lines (was {len(lines)}); backup {backup.name}")
        for dropped_id, kept_id in lost_ids:
            status = ledger_dismiss_housekeeping(path, dropped_id, kept_id,
                                                 "org_dedup_within_file")
            print(f"    ledger {dropped_id}: {status}")
    return removed, removed_lines, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true", help="rewrite the file(s)")
    a = ap.parse_args()

    tot_r = tot_l = tot_s = 0
    for f in a.files:
        if not f.exists():
            print(f"  MISSING {f}", file=sys.stderr)
            return 2
        r, l, s = dedup(f, a.apply)
        tot_r += r
        tot_l += l
        tot_s += s

    print()
    verb = "removed" if a.apply else "would remove"
    print(f"  {verb} {tot_r} duplicate subtree(s), {tot_l} line(s)")
    if tot_s:
        print(f"  {tot_s} group(s) LEFT ALONE — same heading, different bodies; "
              f"resolve by hand")
    if not a.apply and tot_r:
        print("  dry run — re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
