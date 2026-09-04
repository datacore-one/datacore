#!/usr/bin/env python3
"""Flip one space to Phase 1, or reverse it -- the drill's steps, on a real space.

FLIP     write `.datacore/ledger-phase` = 1, ignore org/next_actions.org,
         drop it from the index, generate it from the ledger, commit.
REVERSE  remove the marker and the ignore line, commit the current generated
         file as the authored file again.

Preconditions for FLIP, checked, not assumed: the ledger folds; the space's
last ingest left nothing unimported; org and projection agree on every live
item (the shadow diff is clean for this space). Refuses otherwise.

    ledger_phase1_flip.py --space NAME (--flip | --reverse) [--root DIR] [--apply]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger_project_org import MARKER, ORG, phase, project_space  # noqa: E402

IGNORE_LINE = "org/next_actions.org"


def git(space: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(space), *args], capture_output=True, text=True)


def shadow_clean(space: Path) -> tuple[bool, str]:
    from ledger_checkpoint import _fingerprint  # noqa: E402
    from ledger.fold import fold
    from ledger.log import read_events
    from ledger.genesis import scan
    live = _fingerprint(fold(read_events(space)))
    before = scan(space)
    unimported = len(before.importable)
    ids_in_org = set()
    for f in (space / "org").glob("*.org"):
        for line in f.read_text(errors="replace").splitlines():
            if line.strip().startswith(":ID:"):
                ids_in_org.add(line.split(":ID:", 1)[1].strip())
    extra = sorted(set(live) - ids_in_org)
    if unimported:
        return False, f"{unimported} org task(s) not yet in the ledger — ingest first"
    if extra:
        return False, f"{len(extra)} live ledger item(s) with no org task — run ledger_phase1_prepare first"
    return True, f"{len(live)} live items agree"


def flip(space: Path, apply: bool) -> int:
    ok, why = shadow_clean(space)
    print(f"  precondition: {why}")
    if not ok:
        return 1
    if phase(space) == 1:
        print("  already Phase 1"); return 0
    if not apply:
        print("  dry run — would write marker, ignore + untrack org/next_actions.org, generate, commit"); return 0
    (space / MARKER).parent.mkdir(parents=True, exist_ok=True)
    (space / MARKER).write_text("1\n")
    gi = space / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if IGNORE_LINE not in lines:
        gi.write_text("\n".join(lines + ["# Phase 1 (DIP-0046): generated from the ledger, never authored", IGNORE_LINE]) + "\n")
    git(space, "rm", "-q", "--cached", str(ORG))
    print("  " + project_space(space))
    git(space, "add", ".gitignore", str(MARKER))
    r = git(space, "commit", "-q", "-m", "phase 1: org/next_actions.org is generated from the ledger (DIP-0046)")
    if r.returncode:
        print("  commit refused:\n" + (r.stderr or r.stdout)[-400:]); return 1
    print(f"  flipped: {git(space, 'log', '--oneline', '-1').stdout.strip()[:70]}")
    return 0


def reverse(space: Path, apply: bool) -> int:
    if phase(space) != 1:
        print("  not in Phase 1"); return 0
    if not apply:
        print("  dry run — would remove marker + ignore line and commit the generated file as authored"); return 0
    print("  " + project_space(space))
    (space / MARKER).unlink(missing_ok=True)
    gi = space / ".gitignore"
    keep = [l for l in gi.read_text().splitlines() if l != IGNORE_LINE and "Phase 1 (DIP-0046)" not in l]
    gi.write_text("\n".join(keep) + ("\n" if keep else ""))
    git(space, "add", ".gitignore", str(ORG))
    git(space, "rm", "-q", "--cached", "--ignore-unmatch", str(MARKER))
    r = git(space, "commit", "-q", "-m", "phase 0: org/next_actions.org is authored again (projection committed as the file)")
    if r.returncode:
        print("  commit refused:\n" + (r.stderr or r.stdout)[-400:]); return 1
    print(f"  reversed: {git(space, 'log', '--oneline', '-1').stdout.strip()[:70]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")))
    ap.add_argument("--space", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--flip", action="store_true"); g.add_argument("--reverse", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    space = a.root / a.space
    return flip(space, a.apply) if a.flip else reverse(space, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
