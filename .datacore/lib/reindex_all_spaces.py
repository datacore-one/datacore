#!/usr/bin/env python3
"""Nightly search reindex for EVERY space, not one.

The reindex job ran `zettel_processor.py --full-process --space personal`, so
every other space's search index stopped at 2026-09-19 (found 2026-09-26).
This runs the same full process per discovered space (zettel_db.SPACES), keeps
going when one space fails, prints one line per space, and exits 1 if any
space failed so the job's contract sees it.

Usage: reindex_all_spaces.py   (flags are passed through to each run)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
PASS_THROUGH = ["--no-stubs", "--no-backlinks"]


def discover() -> list[str]:
    sys.path.insert(0, str(LIB))
    from zettel_db import SPACES
    return list(SPACES)


def index_space(space: str, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, str(LIB / "zettel_processor.py"), "--full-process", "--space", space,
           *(extra if extra is not None else PASS_THROUGH)]
    return subprocess.run(cmd, cwd=LIB.parents[1]).returncode


def main(argv: list[str]) -> int:
    failed = []
    for space in discover():
        rc = index_space(space) if not argv else index_space(space, argv)
        print(f"reindex {space}: {'ok' if rc == 0 else f'FAILED rc={rc}'}", flush=True)
        if rc != 0:
            failed.append(space)
    print(f"reindex: {len(failed)} space(s) failed" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
