#!/usr/bin/env python3
"""A restorable checkpoint of the ledger, and proof that it restores.

After the Phase 1 flip the org file is GENERATED, which quietly removes the
fallback everyone assumes is still there: re-running genesis against it would
re-derive the ledger from a file the ledger just produced. Corruption gets
laundered rather than repaired. Re-genesis is only cheap while something
authored-shaped survives, and this is that thing.

Two operations, and the second is the point:

  write    render the ledger to a checkpoint org file, tracked in git so it
           survives losing the machine.

  verify   import that checkpoint into a THROWAWAY space, fold it, and compare
           the result against the live ledger item by item. This answers "could
           we actually rebuild from this?" — which is not the same question as
           "does a checkpoint file exist", and is the only one worth asking. A
           backup nobody has restored is a claim (DIP-0046 F2a, same lesson).

WHERE IT LIVES, AND WHY IT MATTERS MORE THAN IT LOOKS.

`.datacore/checkpoints/`, NEVER inside `org/`. A checkpoint reproduces every
`:ID:` by construction — that is what makes it restorable — so putting it beside
the authored file is precisely the bug that cost 1,204 rewritten ids on
2026-08-12: any tool loading more than one org file from that directory sees
every id twice, `dedup_ids()` regenerates on load, a save persists it, and
autosave commits and pushes it. Tooling globs `<space>/org/*.org`; this
directory is not on that path.

Deliberately NOT gitignored, unlike the projection. The projection is derived
and disposable; this is the restore point, and a restore point that dies with
the machine is not one.

    ledger_checkpoint.py write  [--space NAME] [--root DIR]
    ledger_checkpoint.py verify [--space NAME] [--root DIR]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.genesis import import_space  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.projector import project  # noqa: E402

LIVE = ("created", "claimed", "granted")
CHECKPOINT_REL = Path(".datacore") / "checkpoints" / "next_actions.org"


def _default_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _fingerprint(state) -> dict[str, tuple]:
    """The fields a restore must preserve. Not the state root: a fresh import
    writes new events with new hashes and hlcs, so the CHAIN differs by design
    — what must survive is the ITEMS.

    Tags are compared as EFFECTIVE tags — the item's own plus whatever it
    inherits — because own-tags alone flag a correct transformation as damage.

    When a task's parent is missing from a projection, the projector promotes
    it to top level and writes its effective tags, so the tags it used to
    inherit are not silently dropped (see projector.py). That is right: the
    item keeps every tag it had. But its OWN tag set legitimately grows, and
    comparing own-tags reported nine such items across 0-personal and 5-plur as
    "altered" — a restore that preserved the tags perfectly, described as one
    that corrupted them.

    Effective tags are invariant under that promotion, which is exactly the
    property a restore must hold: the item ends up tagged the same way, whether
    or not the parent came along.

    FILE-LEVEL tags are subtracted from both sides. `#+FILETAGS: :gtd:` applies
    to every item in the file, so it carries no per-item information — but the
    two sides disagree about it: a recorded `effective_tags` omits filetags,
    while re-importing a projection that reproduces the FILETAGS line picks
    them up. That asymmetry reported 16 untagged items in 0-personal as
    "altered", every one of them differing by exactly the same constant.
    Subtracting it compares what is actually per-item.
    """
    out = {}
    for iid, item in state.items.items():
        if item.status not in LIVE:
            continue
        p = item.payload or {}
        eff = set(p.get("effective_tags") or p.get("tags") or [])
        eff -= set(p.get("filetags") or [])
        out[iid] = (p.get("title"), p.get("state"),
                    tuple(sorted(eff)),
                    p.get("scheduled"), p.get("deadline"))
    return out


def write(space: Path) -> Path:
    state = fold(read_events(space))
    text = project(state, space=space.name).text
    dest = space / CHECKPOINT_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".org.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)          # atomic: a half-written restore point is worse
    return dest                # than none, and this file only matters in a crisis


def verify(space: Path) -> tuple[bool, str]:
    """Rebuild from the checkpoint in a scratch space and compare.

    STALENESS IS NOT CORRUPTION, and conflating them made this tool lie.

    The checkpoint on disk is written once a day. Every item appended to the
    ledger after that write is, trivially, absent from it — so comparing the
    stored file against the CURRENT ledger reported ordinary new work as data
    loss. On 2026-08-13 that read as "4 of 9 spaces would NOT restore, 23 items
    lost". Re-writing first and re-running dropped it to 1 lost item: 22 of the
    23 were tasks created since breakfast.

    That is the worst failure mode available to this particular tool. Its whole
    purpose is answering "could we actually re-genesis from this?", and an
    answer that cries corruption on a healthy system is one the operator learns
    to wave away — leaving nothing to raise the alarm when a restore genuinely
    breaks.

    So project FRESH from the same ledger being compared against. That isolates
    the question this is meant to answer — does the projection round-trip? —
    from "is the file on disk current?", which is the write step's job and is
    reported separately below.
    """
    cp = space / CHECKPOINT_REL
    if not cp.is_file():
        return False, "no checkpoint written yet"

    state = fold(read_events(space))
    live = _fingerprint(state)

    # Round-trip a projection of the CURRENT state, not yesterday's file.
    fresh = project(state, space=space.name).text
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / space.name
        (scratch / ".datacore" / "events").mkdir(parents=True)
        (scratch / "org").mkdir()
        (scratch / "org" / "next_actions.org").write_text(fresh, encoding="utf-8")
        import_space(scratch, org_file=scratch / "org" / "next_actions.org")
        restored = _fingerprint(fold(read_events(scratch)))

    missing = sorted(set(live) - set(restored))
    extra = sorted(set(restored) - set(live))
    changed = [i for i in (set(live) & set(restored)) if live[i] != restored[i]]

    # Report the on-disk file's age as its own fact. A stale checkpoint is a
    # real problem — it is what a restore would actually start from — but it is
    # a DIFFERENT problem from a projection that cannot round-trip, and the two
    # need different fixes: run the write step, versus fix the projector.
    stale = ""
    if cp.read_text(encoding="utf-8", errors="replace") != fresh:
        stale = " [on-disk checkpoint is behind the ledger — run: write]"

    if not (missing or extra or changed):
        return True, f"{len(live)} item(s) restore identically{stale}"
    parts = []
    if missing:
        parts.append(f"{len(missing)} lost (e.g. {missing[0]})")
    if extra:
        parts.append(f"{len(extra)} invented")
    if changed:
        parts.append(f"{len(changed)} altered (e.g. {changed[0]})")
    return False, "; ".join(parts) + stale


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["write", "verify"])
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--space")
    a = ap.parse_args()

    spaces = [s for s in sorted(a.root.glob("[0-9]-*"))
              if (s / ".datacore" / "events").is_dir()
              and (not a.space or s.name == a.space)]
    if not spaces:
        print(f"ERROR: no spaces with a ledger under {a.root} — refusing to report success")
        return 2

    bad = 0
    for space in spaces:
        if a.op == "write":
            dest = write(space)
            print(f"  {space.name:<14} checkpoint -> {dest.relative_to(space)}")
        else:
            ok, detail = verify(space)
            print(f"  {'ok  ' if ok else 'FAIL'} {space.name:<14} {detail}")
            bad += 0 if ok else 1

    if a.op == "verify":
        print(f"\ncheckpoint-verify: {len(spaces)} space(s), {bad} that would NOT restore")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
