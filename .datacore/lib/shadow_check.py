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

# Days of clean shadow required before the Phase 1 flip. Operator-set to 5
# (2026-08-12): 14 was a round number I picked, never derived from anything —
# see the note below on what the gate actually measures. The counter is now
# honest about consecutive days, so a shorter window is a real 5 days rather
# than 14 runs spread over a month.
#
# What this still does NOT measure: coverage. Five quiet days prove less than
# three busy ones. The classes that have actually broken org<->ledger
# correspondence are ID churn, un-ingested captures, multi-machine converge,
# org-side state changes and merge conflicts. A streak that never met any of
# them is weak evidence whatever its length.
PHASE1_CLEAN_DAYS = int(os.environ.get("DATACORE_PHASE1_DAYS", "5"))


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
    today = date.today()
    today_s = today.isoformat()
    prev_date = prev.get("date")

    # CONSECUTIVE MEANS CONSECUTIVE. This used to increment whenever the last
    # run carried any date other than today's, so it counted RUNS ON DISTINCT
    # DATES, not consecutive days. A laptop that sleeps through the scheduled
    # window — this one slept through 07:40 and 07:50 on 2026-08-12 and macOS
    # cron never catches up — would skip days silently and still reach 14,
    # certifying a fortnight that was never checked. That is the exact defect
    # DIP-0046 exists to remove, sitting inside the gate that authorises the
    # Phase 1 flip.
    #
    # A gap now RESETS to 1: today is clean, but the chain behind it is not
    # evidence. Better a slow honest counter than a fast dishonest one.
    if prev_date != today_s:
        if not all_clean:
            streak = 0
        else:
            gap_ok = False
            if prev_date:
                try:
                    gap_ok = (today - date.fromisoformat(prev_date)).days == 1
                except ValueError:
                    gap_ok = False
            streak = streak + 1 if gap_ok else 1
    elif not all_clean:
        streak = 0                          # a later dirty run same day resets

    STATUS.parent.mkdir(parents=True, exist_ok=True)
    # State roots travel with the diff so two machines can compare agreement in
    # one hash rather than replaying two logs (DIP-0046 §3.7). A mismatch is only
    # meaningful once seq-gap reports no gap — mid-convergence the roots MUST
    # differ, and alarming on that would make this noisy by construction.
    roots = {}
    for name in spaces:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from ledger.fold import fold as _fold
            from ledger.log import read_events as _read
            roots[name] = _fold(_read(Path(name))).state_root()[:16]
        except Exception:      # a root is diagnostic, never load-bearing here
            roots[name] = None

    STATUS.write_text(json.dumps({
        "date": today_s,
        "generated_at": f"{today}",
        "spaces": spaces,
        "clean_spaces": clean,
        "total_spaces": len(spaces),
        "all_clean": all_clean,
        "consecutive_clean_days": streak,
        "state_roots": roots,
    }, indent=2))

    gate = "READY for Phase 1" if streak >= PHASE1_CLEAN_DAYS else \
           f"{PHASE1_CLEAN_DAYS - streak} more clean day(s) to Phase 1"
    print(f"\n  {clean}/{len(spaces)} clean | consecutive clean days: {streak}"
          f" | {gate}")
    return 0 if all_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
