#!/usr/bin/env python3
"""Open each SQLite backup copy and prove it restores (plan 2026-10-31, phase H, backups 0).

    sqlite_restore_check.py COPY.db [COPY.db ...]

The only proof a knowledge-index backup had was the size read back after the
push; a copy nobody opened is a claim. Each copy is opened read-only and must
pass `PRAGMA quick_check`, and every table must hold a row count within 5% of
what the source had at push time -- the counts sqlite_push.py sends beside the
copy as COPY.db.counts.json. No counts file is not a pass.

One line per copy that fails, then the last line, which is the job's evidence:
    restore-check ok N copy(ies): ...
    restore-check FAILED F of N: ...
Exit 0 only when every copy passes.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

TOLERANCE = 0.05


def check(copy: Path) -> str:
    """'' when the copy restores, else why not."""
    side = copy.with_name(copy.name + ".counts.json")
    try:
        want = json.loads(side.read_text())["tables"]
    except (OSError, ValueError, KeyError, TypeError):
        return f"no row counts beside it ({side.name}) -- cannot compare with the source"
    try:
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            qc = con.execute("PRAGMA quick_check").fetchone()[0]
            if qc != "ok":
                return f"quick_check: {qc}"
            have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            bad = []
            for table, n in sorted(want.items()):
                if table not in have:
                    bad.append(f"{table} gone (source had {n})")
                    continue
                got = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if n and abs(got - n) / n > TOLERANCE:
                    bad.append(f"{table} {got} rows, source had {n}")
            return "; ".join(bad)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"does not open: {exc}"


def main(argv: list[str] | None = None) -> int:
    copies = [Path(a).expanduser() for a in (sys.argv[1:] if argv is None else argv)]
    if not copies:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    failed = 0
    for c in copies:
        why = check(c)
        if why:
            failed += 1
            print(f"  FAIL {c}: {why}")
    names = ", ".join(c.name for c in copies)
    if failed:
        print(f"restore-check FAILED {failed} of {len(copies)}: {names}")
        return 1
    print(f"restore-check ok {len(copies)} copy(ies): {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
