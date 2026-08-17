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
being kept. Same-heading entries whose bodies differ are reported and left
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


def dedup(path: Path, apply: bool) -> tuple[int, int, int]:
    """Returns (removed_blocks, removed_lines, skipped_groups)."""
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
    kept: list[list[str]] = []
    removed = removed_lines = skipped = 0
    reports: list[str] = []

    for key, text in subtrees:
        if key not in seen:
            seen[key] = identity(text)
            kept.append(text)
            continue
        if identity(text) == seen[key]:
            removed += 1
            removed_lines += len(text)
            reports.append(f"    drop  {text[0].strip()[:72]}")
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

    if apply and removed:
        backup = path.with_suffix(path.suffix + ".bak-dedup")
        shutil.copy2(path, backup)
        out = preamble + [l for t in kept for l in t]
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"    wrote {len(out)} lines (was {len(lines)}); backup {backup.name}")
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
