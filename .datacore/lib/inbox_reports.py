#!/usr/bin/env python3
"""Reports that wait in a space's 0-inbox/reports/ until a briefing includes them.

Owner decision 2026-10-18 (space-structure board): the trading space's reports
first go into 0-inbox, are included in the daily briefing, and are then moved
to where they live. /today step 11 runs:

    python3 .datacore/lib/inbox_reports.py list --space 6-meridian
    ... include each report in the briefing ...
    python3 .datacore/lib/inbox_reports.py file --space 6-meridian \
        --to 1-tracks/trading/reports <each listed path>

`file` moves only the paths it is given, so a report that arrived while the
briefing was written waits for the next one, and it never replaces a file
already at the destination. The space's knowledge auto-commit records the move.
"""
import argparse
import json
import os
import sys
from pathlib import Path

INBOX = Path("0-inbox") / "reports"
JUNK = {".DS_Store", ".gitkeep"}


def pending(space: Path) -> list[str]:
    """Reports waiting in the inbox, as paths relative to 0-inbox/reports/."""
    base = space / INBOX
    if not base.is_dir():
        return []
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                  if p.is_file() and p.name not in JUNK)


def file_reports(space: Path, to: str, names: list[str]) -> list[str]:
    """Move each listed report to `to`/<same relative path>; return the new paths."""
    base, dest_root = space / INBOX, space / to
    pairs = [(base / n, dest_root / n) for n in names]
    for src, dst in pairs:
        if not src.is_file():
            raise FileNotFoundError(f"not in the inbox: {src.relative_to(space)}")
        if dst.exists():
            raise FileExistsError(f"already filed: {dst.relative_to(space)}")
    moved = []
    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        moved.append(dst.relative_to(space).as_posix())
        parent = src.parent
        while parent != base and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--space", required=True)
    ls.add_argument("--json", action="store_true")
    fl = sub.add_parser("file")
    fl.add_argument("--space", required=True)
    fl.add_argument("--to", required=True, help="destination inside the space, e.g. 1-tracks/trading/reports")
    fl.add_argument("names", nargs="+")
    a = ap.parse_args()
    space = Path(a.space).expanduser().resolve()
    if a.cmd == "list":
        names = pending(space)
        if a.json:
            print(json.dumps(names))
        else:
            print("\n".join(str(space / INBOX / n) for n in names) if names else "no reports waiting")
        return 0
    try:
        for p in file_reports(space, a.to, a.names):
            print(f"filed {p}")
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
