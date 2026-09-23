#!/usr/bin/env python3
"""Where the research pipeline keeps its per-item bookkeeping, and the one-off
migration that moves old items to it (owner decision D9, 2026-09-23).

`:FETCH_ATTEMPTS:`, `:ANALYSIS_ATTEMPTS:` and `:RESULT:` used to be written
directly under the heading, OUTSIDE the item's `:PROPERTIES:` drawer. They
worked, because the orchestrator read them back as text, but org-workspace
`get_property` could not see them. Worse, a line between a heading and its
drawer stops org from recognising the drawer at all, so the item's `:ID:` and
`:SOURCE:` went invisible to every org-mode reader too.

From now on they are written inside the drawer. Readers accept both places
(the drawer wins when both hold a value), so nothing breaks before the
migration runs. This file is also that migration:

    python3 migrate_research_props.py FILE            # dry run: report + diff summary
    python3 migrate_research_props.py FILE --apply    # rewrite FILE under the org lock

Pure line functions, stdlib only; research_orchestrator.py imports them.
A "line list" is `text.split("\\n")`, exactly as the orchestrator uses it.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

MOVED_PROPS = ("FETCH_ATTEMPTS", "ANALYSIS_ATTEMPTS", "RESULT")
PLANNING = ("CLOSED:", "SCHEDULED:", "DEADLINE:")
_HEADING_LINE_RE = re.compile(r"^\*+\s")
_DRAWER_OPEN_RE = re.compile(r"^:([A-Za-z][A-Za-z0-9_-]*):$")
INDENT = "    "


def is_heading(line: str) -> bool:
    """An org heading starts in column 0 with stars and a space."""
    return bool(_HEADING_LINE_RE.match(line))


def section_end(lines: List[str], hi: int) -> int:
    """Index of the next heading after `hi` (or len(lines))."""
    for j in range(hi + 1, len(lines)):
        if is_heading(lines[j]):
            return j
    return len(lines)


def property_drawer(lines: List[str], hi: int) -> Optional[Tuple[int, int]]:
    """(index of `:PROPERTIES:`, index of its `:END:`) inside the item's own
    section, or None."""
    end = section_end(lines, hi)
    for j in range(hi + 1, end):
        if lines[j].strip() == ":PROPERTIES:":
            for k in range(j + 1, end):
                if lines[k].strip() == ":END:":
                    return j, k
            return None
    return None


def _prop_value(line: str, prop: str) -> Optional[str]:
    s = line.strip()
    tag = f":{prop}:"
    if s == tag:
        return ""
    if s.startswith(tag + " ") or s.startswith(tag + "\t"):
        return s[len(tag):].strip()
    return None


def _outside_drawers(lines: List[str], hi: int) -> List[int]:
    """Indices in the item's section that are in no drawer (`:NAME:` … `:END:`)."""
    out, inside = [], False
    for j in range(hi + 1, section_end(lines, hi)):
        s = lines[j].strip()
        if inside:
            inside = s != ":END:"
            continue
        m = _DRAWER_OPEN_RE.match(s)
        if m and m.group(1) not in MOVED_PROPS:
            inside = True
            continue
        out.append(j)
    return out


def legacy_prop_lines(lines: List[str], hi: int, prop: str) -> List[int]:
    """The old location: the property's lines in the section, outside any drawer."""
    return [j for j in _outside_drawers(lines, hi) if _prop_value(lines[j], prop) is not None]


def get_item_prop(lines: List[str], hi: int, prop: str) -> Tuple[Optional[str], str]:
    """(value, where) with where in {"drawer", "legacy", "none"}. The drawer
    wins; otherwise the first line in the old location."""
    d = property_drawer(lines, hi)
    if d:
        for j in range(d[0] + 1, d[1]):
            v = _prop_value(lines[j], prop)
            if v is not None:
                return v, "drawer"
    legacy = legacy_prop_lines(lines, hi, prop)
    if legacy:
        return _prop_value(lines[legacy[0]], prop), "legacy"
    return None, "none"


def ensure_drawer(lines: List[str], hi: int) -> Tuple[Tuple[int, int], bool]:
    """The item's drawer, creating an empty one right after the heading and its
    planning lines when there is none. Returns ((start, end), created)."""
    d = property_drawer(lines, hi)
    if d:
        return d, False
    at = hi + 1
    while at < len(lines) and lines[at].strip().startswith(PLANNING):
        at += 1
    lines[at:at] = [f"{INDENT}:PROPERTIES:", f"{INDENT}:END:"]
    return (at, at + 1), True


def set_item_prop(lines: List[str], hi: int, prop: str, value: str) -> bool:
    """Write `prop` inside the item's drawer and delete it from the old
    location. Mutates `lines`; returns True if a drawer had to be created."""
    for j in reversed(legacy_prop_lines(lines, hi, prop)):
        del lines[j]
    (s, e), created = ensure_drawer(lines, hi)
    for j in range(s + 1, e):
        if _prop_value(lines[j], prop) is not None:
            indent = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
            lines[j] = f"{indent}:{prop}: {value}"
            return created
    indent = lines[s][:len(lines[s]) - len(lines[s].lstrip())]
    lines.insert(e, f"{indent}:{prop}: {value}")
    return created


def migrate_item(lines: List[str], hi: int) -> dict:
    """Move every MOVED_PROPS line of one item into its drawer. A value the
    drawer already holds wins (it is what readers see) and the old line is
    dropped. Returns counts; mutates `lines`."""
    r = {"moved": 0, "drawers_created": 0, "dropped_duplicates": 0}
    for prop in MOVED_PROPS:
        legacy = legacy_prop_lines(lines, hi, prop)
        if not legacy:
            continue
        value, where = get_item_prop(lines, hi, prop)
        if where == "drawer":
            r["dropped_duplicates"] += len(legacy)
            for j in reversed(legacy):
                del lines[j]
            continue
        r["moved"] += 1
        r["dropped_duplicates"] += len(legacy) - 1
        if set_item_prop(lines, hi, prop, value or ""):
            r["drawers_created"] += 1
    return r


def migrate_text(text: str) -> Tuple[str, dict]:
    """The whole file migrated, and a report. Idempotent."""
    lines = text.split("\n")
    total = {"moved": 0, "items": 0, "drawers_created": 0, "dropped_duplicates": 0}
    hi = 0
    while hi < len(lines):
        if is_heading(lines[hi]):
            r = migrate_item(lines, hi)
            if r["moved"] or r["dropped_duplicates"]:
                total["items"] += 1
            for k in ("moved", "drawers_created", "dropped_duplicates"):
                total[k] += r[k]
        hi += 1
    return "\n".join(lines), total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the file (default: dry run, nothing written)")
    args = ap.parse_args(argv)
    text = args.file.read_text(encoding="utf-8")
    new, report = migrate_text(text)
    diff = list(difflib.unified_diff(text.split("\n"), new.split("\n"), lineterm="", n=0))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    mode = "APPLY" if args.apply else "DRY RUN (nothing written)"
    print(f"{mode}: {args.file}")
    print(f"  items touched {report['items']}, properties moved {report['moved']}, "
          f"drawers created {report['drawers_created']}, "
          f"duplicate old lines dropped {report['dropped_duplicates']}")
    print(f"  diff: +{added} -{removed} lines; {len(text.split(chr(10)))} -> {len(new.split(chr(10)))} lines")
    if new == text or not args.apply:
        return 0
    lib = Path(__file__).resolve().parents[3] / "lib"
    sys.path.insert(0, str(lib))
    from org_transaction import serialized, watch_file, write_org_text

    @serialized
    def _apply() -> bool:
        watch_file(args.file)
        current = args.file.read_text(encoding="utf-8")
        if current != text:
            return False                      # changed since the dry run above: redo
        write_org_text(args.file, new)
        return True

    if not _apply():
        print("  REFUSED: the file changed while migrating; run again")
        return 1
    print("  written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
