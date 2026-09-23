#!/usr/bin/env python3
"""Union two diverged versions of an Org file by :ID:, losing no item.

WHY NOT A TEXT MERGE. When two machines both capture into the same Org file and
cannot sync for a while, git sees one enormous conflicted hunk and every
resolution it offers is lossy: `--ours` and `-X theirs` each discard whatever
the other side captured. Measured 2026-09-16 on 0-personal/org/inbox.org --
241 items existed only on the executor host and 140 only on origin. Any
side-pick silently threw away hundreds of real captures.

An Org file is not lines, it is a set of items with identities. Keyed on `:ID:`
the merge is almost entirely mechanical: an item on one side is kept, an item on
both sides is kept once. Only an item that exists on BOTH sides with DIFFERENT
content needs a rule, and in practice there are very few -- one, in that same
measurement.

THE ONE RULE, and its limit. A recorded closure is strictly later information
than the open state it closes: if one side has a terminal state with a `CLOSED:`
stamp and the other does not, the closed one wins. Anything else -- both closed
and differing, neither closed and differing -- is a real disagreement this tool
REFUSES rather than guesses at, naming the ids so a human can look. Refusing is
the point: a merge tool that always produces an answer is how the hundreds of
items got lost in the first place.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import org_transaction

HEADING = re.compile(r"^(\*+) ")
ID = re.compile(r"^\s*:ID:\s*(\S+)\s*$", re.M)
#: DIP-0009 v2.0 terminal states. DEFERRED is closed-but-wakeable, not terminal.
TERMINAL = ("DONE", "CANCELLED")
CLOSED = re.compile(r"^\s*CLOSED:\s*\[", re.M)
#: Stars and an optional state keyword at the start of a heading line.
_STARS_STATE = re.compile(
    r"^\*+\s+(?:(?:TODO|NEXT|WAITING|REVIEW|DONE|DEFERRED|CANCELLED)(?:\s+|$))?")


def split(text: str) -> tuple[str, list[tuple[str | None, str]]]:
    """(preamble, [(id-or-None, block)]) preserving file order and bytes."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if HEADING.match(l)]
    if not starts:
        return text, []
    preamble = "".join(lines[: starts[0]])
    blocks: list[tuple[str | None, str]] = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = "".join(lines[s:e])
        found = ID.search(block)
        blocks.append((found.group(1) if found else None, block))
    return preamble, blocks


def _closed(block: str) -> bool:
    head = block.splitlines()[0] if block else ""
    state = head.split(None, 2)[1] if len(head.split(None, 2)) > 1 else ""
    return state in TERMINAL and bool(CLOSED.search(block))


def _level(block: str) -> int:
    m = HEADING.match(block)
    return len(m.group(1)) if m else 0


def _section_line(block: str, level: int) -> str:
    """A plain heading at `level` with the text of `block`'s heading.

    No state keyword, no drawer, no body: a created heading is structure,
    never a task, and never carries an :ID: (the original keeps its id)."""
    head = block.splitlines()[0] if block else ""
    return "*" * level + " " + _STARS_STATE.sub("", head).rstrip() + "\n"


#: Text of the heading(s) created for a theirs-only item that has no parent in
#: theirs but sits deeper than level 1 (decision Q14, 2026-09-23). Neutral on
#: purpose: it names the tool, carries no state keyword, priority, tag or
#: :ID:, and is made unique against every heading of both sides (`_unfiled`).
UNFILED = "Unfiled by org_union_merge"
_PRIORITY_TAGS = re.compile(r"^\[#.\]\s*|\s+:[\w:@#%-]+:\s*$")


def _heading_key(block: str) -> str:
    """A heading's text as a collision key: no stars, state, priority or tags,
    whitespace collapsed, case folded."""
    head = block.splitlines()[0] if block else ""
    return " ".join(_PRIORITY_TAGS.sub("", _STARS_STATE.sub("", head).strip()).split()).casefold()


def _unfiled(*sides: list[tuple[str | None, str]]) -> str:
    """UNFILED, or UNFILED (2), (3)... -- the first that no heading uses."""
    taken = {_heading_key(b) for side in sides for _, b in side}
    n, text = 1, UNFILED
    while text.casefold() in taken:
        n += 1
        text = f"{UNFILED} ({n})"
    return text


def _heading_line(text: str, level: int) -> str:
    return "*" * level + " " + text + "\n"


def _same_unidentified(block: str) -> str:
    """Match key for a heading with no :ID:. Exact bytes, except that trailing
    whitespace is not content: "* Inbox\n" and "* Inbox\n\n" are one section."""
    return block.rstrip() + "\n"


def reconcile(a: str, b: str) -> tuple[str, dict]:
    """Union `a` (ours) with `b` (theirs). Raises ValueError on a real clash.

    Loses nothing: every block of either side is in the result (an unidentified
    block as often as the side that has more copies of it; they are matched as a
    multiset, never by substring -- "** TODO x" is a substring of "*** TODO x"
    and is a different entry). Keeps structure: a block only theirs has goes
    at the end of its theirs-parent's subtree, so it keeps its parent; a
    theirs-only top-level block goes at the end of the file.

    NEVER UNDER AN UNRELATED HEADING (decision G12, 2026-09-23: "a new heading
    should be created, don't put under random heading"). Placing a block at
    the end of its parent's subtree is only right when nothing in that subtree
    sits shallower than the block. When theirs skips a level (`* P` then
    `*** x`) and ours has `** y` under P, the block would read as a child of
    `y`. And when ours' copy of the parent sits at or below the block's level
    (a closure won at another depth), the block cannot be its child at all. In
    both cases the missing heading(s) are CREATED: at the end of the parent's
    subtree for the levels between them, or at the end of the file for levels
    1.. when the parent is too deep. Each created heading reuses the text of
    theirs' own ancestor at that level, or of theirs' parent where theirs has
    none, so the block's new parent always has its theirs-parent's text
    (proved: `placeTheirs_parent_text` in DatacoreSpec/OrgTools.lean).
    Created headings carry no state and no :ID:, and theirs-only siblings
    share one. `stats["headings_created"]` counts them.

    NO PARENT IN THEIRS, BUT DEEPER THAN LEVEL 1 (decision Q14, 2026-09-23:
    "create a heading", same rule as G12). Theirs' file opens with `** x`.
    At end of file `x` would read as a child of the last shallower heading
    there, an unrelated one. It gets created heading(s) instead, with the
    neutral text `UNFILED` ("Unfiled by org_union_merge", or "... (2)",
    "(3)" when a heading of either side already uses it, compared without
    stars, state, priority, tags or case): levels 1..x-1 at end of file, and
    later parentless blocks go under that same root (a level-2 created
    heading is shared by level-3 siblings, as above). No state keyword, no
    :ID:. When the output has no heading shallower than `x` at all (a flat
    file of `**` items), end of file gives `x` no parent, exactly as in
    theirs, and nothing is created. Proved: `placeOrphan_parent_neutral`,
    `placeOrphan_never_existing`, `placeOrphan_keeps`.
    """
    pre_a, items_a = split(a)
    _, items_b = split(b)
    by_b = {i: blk for i, blk in items_b if i}

    # Output entries are small lists [block] so that an entry can be found
    # again by identity after later insertions shift the indices.
    out: list[list[str]] = []
    entry_by_id: dict[str, list[str]] = {}
    unidentified_pool: dict[str, list[list[str]]] = {}
    seen: set[str] = set()
    stats = {"kept_ours": 0, "kept_theirs": 0, "shared": 0, "closure_wins": 0,
             "unidentified": 0, "headings_created": 0}
    clashes: list[str] = []

    for iid, block in items_a:
        entry = [block]
        out.append(entry)
        if iid is None:
            unidentified_pool.setdefault(_same_unidentified(block), []).append(entry)
            stats["unidentified"] += 1
            continue
        seen.add(iid)
        entry_by_id.setdefault(iid, entry)
        other = by_b.get(iid)
        if other is None or other == block:
            stats["shared" if other is not None else "kept_ours"] += 1
            continue
        if _closed(other) and not _closed(block):
            entry[0] = other; stats["closure_wins"] += 1
        elif _closed(block) and not _closed(other):
            stats["closure_wins"] += 1
        else:
            clashes.append(iid)

    def subtree_end(at: int) -> int:
        level = _level(out[at][0])
        end = at + 1
        while end < len(out) and _level(out[end][0]) > level:
            end += 1
        return end

    def ancestors(k: int) -> dict[int, str]:
        """level -> block of theirs' ancestors of theirs' k-th block."""
        found, bound = {}, _level(items_b[k][1])
        for j in range(k - 1, -1, -1):
            lvl = _level(items_b[j][1])
            if lvl < bound:
                found[lvl] = items_b[j][1]
                bound = lvl
        return found

    # (theirs-parent index, level) -> the created heading that stands in for
    # that parent at level-1, so theirs-only siblings share one heading.
    created: dict[tuple[int, int], list[str]] = {}

    unfiled: list[str] = []          # the neutral text, chosen on first use

    def place(k: int, entry: list[str], parent: int | None) -> None:
        level = _level(entry[0])
        if parent is None:
            if level <= 1:
                out.append(entry)           # a top-level block: end of file
                return
            # Decision Q14: no theirs-parent, but deeper than level 1. At end
            # of file it would read as a child of the last shallower heading,
            # so it gets created neutral heading(s) instead: a fresh chain of
            # levels 1..level-1 at end of file, or, once one exists, the same
            # placement as a theirs-only child of that root (siblings share).
            # When the output has NO heading shallower than it (a flat file of
            # `**` items), end of file gives it no parent at all, exactly as
            # in theirs, and nothing is created.
            root = created.get((None, 2))
            if root is None and all(_level(e[0]) >= level for e in out):
                out.append(entry)           # no heading it could fall under:
                return                      # parentless here as in theirs
            if not unfiled:
                unfiled.append(_unfiled(items_a, items_b))
            if root is None:
                chain = [[_heading_line(unfiled[0], m)] for m in range(1, level)]
                out.extend(chain + [entry])
                stats["headings_created"] += len(chain)
                created[(None, 2)] = chain[0]
                created[(None, level)] = chain[-1]
                return
        stand_in = created.get((parent, level))
        if parent is None:
            anchor = stand_in if stand_in is not None else root
        else:
            anchor = stand_in if stand_in is not None else placed[parent]
        at = next(i for i, e in enumerate(out) if e is anchor)
        end = subtree_end(at)
        a_level = _level(anchor[0])
        if a_level < level and all(_level(e[0]) >= level for e in out[at + 1:end]):
            out.insert(end, entry)          # nothing shallower in the way
            return
        # Create the missing heading(s): below the anchor when it is shallower
        # than the block, otherwise a fresh chain from level 1 at end of file.
        lo, pos = (a_level + 1, end) if a_level < level else (1, len(out))
        if parent is None:
            # Only below the unfiled root (level 1 < level): never at EOF.
            chain = [[_heading_line(unfiled[0], m)] for m in range(lo, level)]
        else:
            anc = ancestors(k)
            par_block = items_b[parent][1]
            chain = [[_section_line(anc.get(m, par_block) if m < level - 1 else par_block, m)]
                     for m in range(lo, level)]
        out[pos:pos] = chain + [entry]
        stats["headings_created"] += len(chain)
        created[(parent, level)] = chain[-1]

    # placed[k]: the output entry that stands for theirs' k-th block.
    placed: list[list[str]] = []
    for k, (iid, block) in enumerate(items_b):
        if iid is not None and iid in seen:
            placed.append(entry_by_id[iid]); continue
        if iid is None:
            pool = unidentified_pool.get(_same_unidentified(block))
            if pool:
                placed.append(pool.pop(0)); continue
        level = _level(block)
        parent = next((j for j in range(k - 1, -1, -1)
                       if _level(items_b[j][1]) < level), None)
        entry = [block]
        place(k, entry, parent)
        placed.append(entry)
        if iid is not None:
            seen.add(iid); entry_by_id[iid] = entry
        stats["kept_theirs" if iid is not None else "unidentified"] += 1

    if clashes:
        raise ValueError("items differ on both sides with no closure to decide "
                         "them: " + ", ".join(sorted(clashes)))
    return pre_a + "".join(e[0] for e in out), stats


def _show(ref: str, path: str, repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot read {ref}:{path}: {r.stderr.strip()}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="org file, relative to the repo")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--ours", default="HEAD")
    ap.add_argument("--theirs", default="origin/main")
    ap.add_argument("--apply", action="store_true", help="write the result (default: report)")
    args = ap.parse_args()
    if args.apply:
        return org_transaction.serialized(_run)(args)
    return _run(args)


def _run(args) -> int:
    """Report, and with --apply write, the union. --apply runs under the org
    lock (owner decision Q12, 2026-09-23): the target is watched BEFORE the
    refs are read and written with `write_org_text`, so an adapter commit
    cannot land between the read and the write, and one that raced in is
    refused (stale check) rather than silently overwritten. What the tool
    overwrites by design is unchanged: the working-tree file becomes the union
    of the two refs, whatever uncommitted content it had."""
    target = args.repo / args.path
    if args.apply:
        org_transaction.watch_file(target)
    ours = _show(args.ours, args.path, args.repo)
    theirs = _show(args.theirs, args.path, args.repo)
    try:
        merged, stats = reconcile(ours, theirs)
    except ValueError as exc:
        print(f"REFUSED — {exc}", file=sys.stderr)
        return 2

    before = len(split(ours)[1]), len(split(theirs)[1])
    print(f"{args.path}: {before[0]} + {before[1]} -> {len(split(merged)[1])} item(s)")
    for k, v in stats.items():
        if v:
            print(f"  {k.replace('_', ' ')}: {v}")
    if args.apply:
        org_transaction.write_org_text(target, merged)
        print(f"wrote {target}")
    else:
        print("re-run with --apply to write it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
