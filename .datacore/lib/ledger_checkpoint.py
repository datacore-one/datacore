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
from ledger.projector import _clean_title_and_tags, _org_stamp, project, projected_items  # noqa: E402

# The projector's own notion of live. Since 2026-09-06 `completed` is live
# (an agent finished, nobody signed off: REVIEW in org), so a round-trip
# through the projection carries those items and this side must count them
# too — with the state the projector renders, or every completed item comes
# back "invented" and 2-datacore reports 58 of them.
from ledger.projector import LIVE_STATUSES as LIVE  # noqa: E402
CHECKPOINT_REL = Path(".datacore") / "checkpoints" / "next_actions.org"


def _default_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _rendered_tags(state, known: set[str], iid: str) -> set[str]:
    """The tags the projection carries for `iid`, derived from the LEDGER.

    Org tag inheritance follows physical nesting, and the projector nests a
    child under its parent whenever that parent is itself projected. So the
    tags an item comes back with are its own (as rendered) plus every rendered
    ancestor's own -- and that is computable from the ledger's `parent` links
    without trusting anything recorded at import time.

    The recorded `effective_tags` snapshot is NOT that. It is what the source
    file said when the item was ingested, and nothing refreshes it when the
    parent's tags change or the item is re-filed: 0-personal's
    org-20260811-191903 was captured in inbox.org, later parented under
    `* Routed from inbox :inbox:routed:`, and kept a snapshot without those two
    tags -- so a projection that correctly nested it under that heading was
    reported as having corrupted it (2026-08-31, together with five 5-plur
    items whose parent had gained `AI`). Comparing the snapshot asked the
    restore to reproduce a state the ledger no longer describes.

    The one place the snapshot IS the truth is a promoted orphan: its parent is
    not in the file, so the projector writes the snapshot as the item's own tags
    (see projector.py). Mirrored here, for the same reason -- and mirrored for
    ANCESTORS too: a child under a promoted parent inherits that parent's
    rendered snapshot, not its declared tags. 3-fds org-2cd3c1c78434 (level 4,
    under a level-3 task whose own parent had closed) came back with the four
    tags its promoted parent now carries on its heading; a walk that read the
    parent's declared tags reported that correct inheritance as an alteration.
    """
    tags = _heading_tags(state, known, iid)
    cur, seen = (state.items[iid].payload or {}).get("parent"), set()
    while cur and cur in known and cur not in seen:
        seen.add(cur)
        tags |= _heading_tags(state, known, cur)
        cur = (state.items[cur].payload or {}).get("parent")
    return tags


def _heading_tags(state, known: set[str], iid: str) -> set[str]:
    """The tags the projector writes on `iid`'s own heading: its declared tags,
    or -- when its parent is not projected and it is promoted to top level --
    its recorded effective set, so the tags it used to inherit travel with it.
    Same rule as projector.project(); same normalisation as render_item()."""
    item = state.items[iid]
    p = item.payload or {}
    parent = p.get("parent")
    base = p.get("tags")
    if parent and parent not in known:
        base = p.get("effective_tags") or p.get("tags") or []
    return set(_clean_title_and_tags(item.title, base)[1])


def _fingerprint(state, space_filetags: set | None = None,
                 space: str | None = None) -> dict[str, tuple]:
    """The fields a restore must preserve. Not the state root: a fresh import
    writes new events with new hashes and hlcs, so the CHAIN differs by design
    -- what must survive is the ITEMS.

    Both sides are reduced to what the PROJECTION renders, because that is the
    only form a restore can travel through. Title and tags go through the
    projector's own normalisation (`_clean_title_and_tags`): a tag block the
    original parser left inside a title is split out, and characters the
    parser cannot read become `_`. Tags are the RENDERED effective set --
    derived from the ledger's parent links, see `_rendered_tags` -- not the
    per-item snapshot recorded at import.

    FILE-LEVEL tags are subtracted from both sides. `#+FILETAGS: :gtd:` applies
    to every item in the file, so it carries no per-item information -- but the
    two sides disagree about it: a promoted orphan's snapshot may carry the
    filetag of the file it was ingested from, while re-importing a projection
    that reproduces the FILETAGS line picks up the projection's. That asymmetry
    reported 16 untagged items in 0-personal as "altered", every one of them
    differing by exactly the same constant. Subtracting it compares what is
    actually per-item.
    """
    known = {i.id for i in projected_items(state, space=space)}
    out = {}
    for iid, item in state.items.items():
        if item.status not in LIVE:
            continue
        p = item.payload or {}
        # SECTIONS ARE DERIVED, NOT STORED. genesis imports a plain heading only
        # as the ANCESTOR of a task that lives under it (`_section_payload`),
        # and its own docstring is explicit that sections are "re-derived, not
        # re-imported". A section whose children have all been closed therefore
        # has nothing to re-derive it, and correctly does not come back.
        #
        # Measuring it as lost was measuring structure as if it were content:
        # one such heading in 2-datacore was the sole reason a space reported
        # "would NOT restore" while every task in it restored perfectly.
        if p.get("section"):
            continue
        eff = _rendered_tags(state, known, iid)
        # Subtract the item's OWN recorded filetags, and also the file the
        # round-trip actually goes through. The restored side is re-imported
        # from a projection carrying next_actions.org's `#+FILETAGS:`, so it
        # always records and subtracts those -- while an item that never sat in
        # next_actions.org has `filetags: None` and subtracts nothing.
        # 0-personal's org-20260831-203052 lives in inbox.org, carries `gtd`
        # (next_actions' filetag) in `tags`, and failed the round-trip on
        # exactly that one tag. Subtracting the space's filetags on BOTH sides
        # compares like with like instead of asking the live side to have
        # recorded something only the projection can know.
        eff -= set(p.get("filetags") or [])
        eff -= (space_filetags or set())
        title = _clean_title_and_tags(item.title, p.get("tags"))[0]
        # Normalise timestamps before comparing. Some writers store a bare
        # `2026-08-14`; the projector renders the valid org form
        # `<2026-08-14 Fri>`. Those denote the SAME date, so comparing the raw
        # strings reported eight items as altered by a restore that preserved
        # them exactly -- the mirror of the bug just fixed in the projector.
        # Normalise STATE the way the projector does. `projector.py` renders
        # `payload.get("state") or "TODO"`, so an item created without a state
        # -- every task the nightshift executor admits, for one -- projects as
        # TODO and re-imports as TODO, while the live side still reads None.
        # Comparing the raw values reported 30 such items in winston's
        # 0-personal as "altered" by a restore that preserved them exactly
        # (2026-08-30), the same false-alarm class as the timestamp and
        # filetag asymmetries above. A missing state MEANS TODO here.
        rendered_state = "REVIEW" if item.status == "completed" else (p.get("state") or "TODO")
        out[iid] = (title, rendered_state,
                    tuple(sorted(eff)),
                    _org_stamp(p.get("scheduled")), _org_stamp(p.get("deadline")))
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


def _space_filetags(space: Path) -> set:
    """The filetags of the file the round-trip re-imports through. Both sides
    are compared with these removed, so an item that never lived in
    next_actions.org is not penalised for lacking a `filetags` record only
    the projection could have given it."""
    na = space / "org" / "next_actions.org"
    try:
        for _l in na.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
            if _l.startswith("#+FILETAGS:"):
                return {x for x in _l.split(":", 1)[1].split(":") if x.strip()}
    except OSError:
        pass
    return set()


def round_trip(state, space_name: str, space_ft: set | None = None):
    """Project `state`, re-import the projection into a throwaway space, and
    return (live_fingerprint, restored_fingerprint, fresh_projection_text).

    The comparison core, separated from the on-disk checkpoint so it can be
    exercised on a synthetic state -- every false alarm this tool has raised
    was a fingerprint asymmetry, and those need a test that does not depend on
    what happens to be in a real space today.
    """
    space_ft = space_ft or set()
    live = _fingerprint(state, space_ft, space=space_name)
    fresh = project(state, space=space_name).text
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / space_name
        (scratch / ".datacore" / "events").mkdir(parents=True)
        (scratch / "org").mkdir()
        (scratch / "org" / "next_actions.org").write_text(fresh, encoding="utf-8")
        import_space(scratch, org_file=scratch / "org" / "next_actions.org")
        restored = _fingerprint(fold(read_events(scratch)), space_ft, space=space_name)
    return live, restored, fresh


def compare(live: dict, restored: dict) -> tuple[bool, str]:
    missing = sorted(set(live) - set(restored))
    extra = sorted(set(restored) - set(live))
    changed = sorted(i for i in (set(live) & set(restored)) if live[i] != restored[i])
    if not (missing or extra or changed):
        return True, f"{len(live)} item(s) restore identically"
    parts = []
    if missing:
        parts.append(f"{len(missing)} lost (e.g. {missing[0]})")
    if extra:
        parts.append(f"{len(extra)} invented")
    if changed:
        parts.append(f"{len(changed)} altered (e.g. {changed[0]})")
    return False, "; ".join(parts)


def verify(space: Path) -> tuple[bool, str]:
    """Rebuild from the checkpoint in a scratch space and compare.

    STALENESS IS NOT CORRUPTION, and conflating them made this tool lie.

    The checkpoint on disk is written once a day. Every item appended to the
    ledger after that write is, trivially, absent from it -- so comparing the
    stored file against the CURRENT ledger reported ordinary new work as data
    loss. On 2026-08-13 that read as "4 of 9 spaces would NOT restore, 23 items
    lost". Re-writing first and re-running dropped it to 1 lost item: 22 of the
    23 were tasks created since breakfast.

    That is the worst failure mode available to this particular tool. Its whole
    purpose is answering "could we actually re-genesis from this?", and an
    answer that cries corruption on a healthy system is one the operator learns
    to wave away -- leaving nothing to raise the alarm when a restore genuinely
    breaks.

    So project FRESH from the same ledger being compared against. That isolates
    the question this is meant to answer -- does the projection round-trip? --
    from "is the file on disk current?", which is the write step's job and is
    reported separately below.
    """
    cp = space / CHECKPOINT_REL
    if not cp.is_file():
        return False, "no checkpoint written yet"

    state = fold(read_events(space))
    live, restored, fresh = round_trip(state, space.name, _space_filetags(space))
    ok, detail = compare(live, restored)

    # Report the on-disk file's age as its own fact. A stale checkpoint is a
    # real problem -- it is what a restore would actually start from -- but it is
    # a DIFFERENT problem from a projection that cannot round-trip, and the two
    # need different fixes: run the write step, versus fix the projector.
    if cp.read_text(encoding="utf-8", errors="replace") != fresh:
        detail += " [on-disk checkpoint is behind the ledger — run: write]"
    return ok, detail


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
