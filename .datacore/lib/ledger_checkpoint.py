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
    — what must survive is the ITEMS."""
    out = {}
    for iid, item in state.items.items():
        if item.status not in LIVE:
            continue
        p = item.payload or {}
        out[iid] = (p.get("title"), p.get("state"),
                    tuple(sorted(p.get("tags") or [])),
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
    """Rebuild from the checkpoint in a scratch space and compare."""
    cp = space / CHECKPOINT_REL
    if not cp.is_file():
        return False, "no checkpoint written yet"

    live = _fingerprint(fold(read_events(space)))
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / space.name
        (scratch / ".datacore" / "events").mkdir(parents=True)
        (scratch / "org").mkdir()
        shutil.copy(cp, scratch / "org" / "next_actions.org")
        import_space(scratch, org_file=scratch / "org" / "next_actions.org")
        restored = _fingerprint(fold(read_events(scratch)))

    missing = sorted(set(live) - set(restored))
    extra = sorted(set(restored) - set(live))
    changed = [i for i in (set(live) & set(restored)) if live[i] != restored[i]]

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
