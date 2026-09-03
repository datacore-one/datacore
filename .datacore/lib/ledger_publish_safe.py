#!/usr/bin/env python3
"""Publish machine-written ledger files from every space repo -- and nothing else.

This Mac published its ledger only when /today ran or someone converged by
hand, so `mac-seq-gap` reported unpublished events every hour by construction
(2026-09-03: 102 events in 1-datafund). winston runs its full sync every 15
minutes; that sync autosaves everything, including a human's half-edited
files, which is right for a server and wrong for the workstation someone is
typing on.

So: a space is converged here ONLY when every dirty tracked path in it is
machine-written ledger state. If anything else is dirty, the space is skipped
and named -- the human's work is never committed behind their back.

    ledger_publish_safe.py            # do it
    ledger_publish_safe.py --dry-run  # say what would happen
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("DATACORE_ROOT", pathlib.Path.home() / "Data"))
TRANSPORT = ROOT / ".datacore" / "lib" / "ledger_transport.py"

# Paths a machine writes and a human never edits by hand. Anything else dirty
# means "someone is working here" and the space is left alone.
MACHINE_WRITTEN = (
    ".datacore/events/",
    ".datacore/state/venture/cadence-log/",
    ".datacore/checkpoints/",
    ".datacore/state/seq-hwm/",
)


def dirty_tracked(space: pathlib.Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(space), "status", "--porcelain", "--untracked-files=no"],
                       capture_output=True, text=True, timeout=60)
    return [line[3:].strip() for line in r.stdout.splitlines() if line.strip()]


def only_machine_written(paths: list[str]) -> bool:
    return bool(paths) and all(p.startswith(MACHINE_WRITTEN) for p in paths)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rc = 0
    for space in sorted(p for p in ROOT.glob("[0-9]-*") if (p / ".git").exists()):
        paths = dirty_tracked(space)
        if not paths:
            continue
        if not only_machine_written(paths):
            human = [p for p in paths if not p.startswith(MACHINE_WRITTEN)]
            print(f"  skip  {space.name}: human work is dirty, not touching it ({', '.join(human[:3])})")
            continue
        if a.dry_run:
            print(f"  would {space.name}: publish {len(paths)} ledger file(s)")
            continue
        r = subprocess.run([sys.executable, str(TRANSPORT), "converge", "--space", space.name],
                           capture_output=True, text=True, timeout=300)
        ok = r.returncode == 0
        print(f"  {'ok   ' if ok else 'FAIL '} {space.name}: {len(paths)} ledger file(s)"
              + ("" if ok else f" -- {(r.stderr or r.stdout).strip()[-160:]}"))
        rc = rc or (0 if ok else 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
