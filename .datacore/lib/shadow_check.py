#!/usr/bin/env python3
"""Phase 0 shadow gate: diff every space's projection against its org file.

The migration's flip to Phase 1 is gated on N consecutive days of a clean
diff. That gate only means something if the diff is actually computed every
day and its result is durable -- so this writes a status artifact a DIP-0035
job contract can check, rather than printing and forgetting.

Writes `~/.datacore/state/shadow-status.json`:
    {"generated_at", "spaces": {...}, "clean_spaces", "total_spaces",
     "all_clean": bool, "consecutive_clean_days": int}

The streak is the gate. It increments only on a day where EVERY space is
clean, and resets to zero on any difference -- one space regressing must not
be averaged away by eight that passed.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.shadow import compare  # noqa: E402

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
STATUS = Path.home() / ".datacore" / "state" / "shadow-status.json"


def main() -> int:
    spaces, clean = {}, 0
    for space in sorted(ROOT.glob("[0-9]-*")):
        if not (space / "org" / "next_actions.org").exists():
            continue
        d = compare(space)
        spaces[space.name] = {
            "org": d.org_count, "projected": d.projection_count,
            "lost": d.only_in_org, "extra": d.only_in_projection,
            "changed": sorted(d.changed), "clean": d.clean,
        }
        clean += 1 if d.clean else 0
        print(f"  {d}")

    all_clean = bool(spaces) and clean == len(spaces)

    prev = {}
    if STATUS.exists():
        try:
            prev = json.loads(STATUS.read_text())
        except (OSError, ValueError):
            prev = {}
    streak = int(prev.get("consecutive_clean_days") or 0)
    today = date.today().isoformat()
    if prev.get("date") != today:          # one increment per day, not per run
        streak = streak + 1 if all_clean else 0
    elif not all_clean:
        streak = 0                          # a later dirty run same day resets

    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({
        "date": today,
        "generated_at": f"{today}",
        "spaces": spaces,
        "clean_spaces": clean,
        "total_spaces": len(spaces),
        "all_clean": all_clean,
        "consecutive_clean_days": streak,
    }, indent=2))

    print(f"\n  {clean}/{len(spaces)} clean | consecutive clean days: {streak}")
    return 0 if all_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
