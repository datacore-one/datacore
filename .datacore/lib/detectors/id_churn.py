#!/usr/bin/env python3
"""Duplicate org `:ID:`s — the trigger that rewrites every id in a file.

On 2026-08-11 winston logged, at 13:44:28:

    WARNING:org_workspace.identifiers:Duplicate ID 'org-20260726-trackb-rescore-verify'
    — regenerated as 'org-20260811-134428-8f87a44a'

`dedup_ids()` runs on `OrgWorkspace.load()`: where it finds a duplicate it keeps
the first and **regenerates the rest**. A later `save()` persists that, and the
15-minute `cos_sync` autosave commits and pushes it. One minute later 1,204
`:ID:` lines had changed and eight of nine spaces had lost ledger↔org
correspondence — 0-personal went from 602 matching ids to zero.

Every part of that chain is working as designed. Dedup is correct: two tasks
sharing an id is worse than a new id. Autosave is correct: committing beats
stashing. The defect is that **nothing watches the trigger**, so a repairable
condition (a handful of duplicates) silently escalates into an unrepairable one
(ids regenerated with a timestamp, which cannot be reproduced).

So this watches the trigger, not the damage. Duplicates are cheap to fix while
they are duplicates and expensive once dedup has fired — the whole value is in
the ordering.

It also reports IDs the ledger knows that org has lost, which is the damage
signature itself, so a churn that happens anyway is visible immediately rather
than at the next projection diff.

Baseline (`--acknowledge`): the orphaned ids present now are recorded, and
later runs report every orphaned id NOT in that set, however few (owner
decision D2, 2026-09-23: the old 25 % noise floor is gone). A baseline file
from before then holds a COUNT per space; it keeps the old behaviour, floor
included, and says so, until `--acknowledge` is re-run on that host.

Exit 0 clean, 1 on duplicates or correspondence loss, 2 on error.

    id_churn.py [--root DIR] [--json] [--acknowledge]
"""
from __future__ import annotations

import argparse
import os
import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

ID_RE = re.compile(r":ID:\s*(\S+)")
ORG_FILES = ("next_actions.org", "inbox.org")


NOISE_FLOOR = 0.25


def scan_space(space: Path, noise_floor: bool = False) -> dict | None:
    """Duplicates and orphaned ledger ids for one space, or None when clean.

    `noise_floor` restores the pre-2026-09-23 rule that orphans under 25 % of
    the open ledger ids are ordinary lifecycle and not reported. Owner decision
    D2 dropped it: with a set baseline, ANY id newly churned since the
    acknowledged set is a finding (DatacoreSpec/Detectors.lean,
    `ic_report_complete`; `ic_floor_hides_new_churn` is the counterexample the
    floor allowed). Only a legacy COUNT baseline still applies it, so those
    hosts keep their old behaviour until `--acknowledge` is re-run.
    """
    dupes: dict[str, int] = {}
    org_ids: set[str] = set()
    for name in ORG_FILES:
        f = space / "org" / name
        if not f.exists():
            continue
        ids = ID_RE.findall(f.read_text(errors="replace"))
        org_ids |= set(ids)
        for i, n in Counter(ids).items():
            if n > 1:
                dupes[i] = dupes.get(i, 0) + n - 1

    orphaned = 0
    orphan_ids: set[str] = set()
    if org_ids:
        # FOLD, don't scan raw creates. Reading item.create alone counts items
        # that were later completed or dismissed — which SHOULD be absent from
        # org, that being what closing a task means. Measured on 7-megaphone:
        # 6 "churned" ids, all six dismissed, 28% of the space and so over the
        # noise floor. A detector that fires on finished work teaches the
        # operator to ignore it, which costs more than it can ever save.
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        try:
            from ledger.fold import fold
            from ledger.log import read_events
            st = fold(read_events(space))
            led = {i for i, it in st.items.items()
                   if it.status in ("created", "claimed", "granted")}
        except Exception:      # noqa: BLE001 — a fold failure is not churn
            return None
        # Ledger ids with no org task. The fold above already drops completed
        # and dismissed items, so what is left is an open item org has lost.
        orphan_ids = led - org_ids
        orphaned = len(orphan_ids)
        if noise_floor and led and orphaned / len(led) < NOISE_FLOOR:
            orphaned = 0        # legacy count baseline only: below the old floor
            orphan_ids = set()
    if not dupes and not orphaned:
        return None
    return {"space": space.name, "duplicates": sum(dupes.values()),
            "examples": sorted(dupes)[:3], "orphaned_ledger_ids": orphaned,
            "orphaned_ids": sorted(orphan_ids)}


def _default_root() -> Path:
    """Root from DATACORE_ROOT, then ~/Data — NEVER from this file's location.

    A second checkout exists for scheduled runs (~/.datacore/v2-runner). A
    location-derived root would make this scan THAT tree, which holds zero
    spaces, and report "0 findings" — a false green, and the same defect
    seq_gap shipped once already as a parents[] off-by-one.
    """
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _load_baseline(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def is_legacy_baseline(baseline: dict) -> bool:
    """True for a baseline written before 2026-09-23: a COUNT per space. Such a
    file keeps the old behaviour, noise floor included (decision D2)."""
    return any(not k.startswith("_") and not isinstance(v, list)
               for k, v in baseline.items())


def apply_baseline(findings: list, baseline: dict) -> list:
    """Drop orphaned ids that were acknowledged; keep duplicates always (they
    are the trigger, never acknowledged).

    GROWTH IS A SET, NOT A COUNT. The baseline used to be a number per space,
    and growth was `orphaned - acknowledged`. Two acknowledged ids repaired and
    two NEW ids churned left the count where it was, so fresh churn read as
    "growth 0" — exactly the new damage the baseline was introduced to keep
    visible (DatacoreSpec/Detectors.lean, `ic_count_masks_churn`). A baseline
    written by `--acknowledge` now lists the ids, and growth is the orphaned
    ids not in that list. A legacy numeric baseline still reads, count-based,
    and says so: re-run `--acknowledge` to get the set.
    """
    out = []
    for r in findings:
        raw = baseline.get(r["space"], 0)
        orphaned = int(r.get("orphaned_ledger_ids") or 0)
        if isinstance(raw, list):
            acked = {str(i) for i in raw}
            new = sorted(set(r.get("orphaned_ids") or []) - acked)
            growth, how = len(new), "by id"
            r = dict(r, orphaned_ledger_ids=growth, orphaned_ids=new)
            ack_n = orphaned - growth
        else:
            ack = int(raw or 0)
            growth, how = max(0, orphaned - ack), "by COUNT (legacy baseline; re-run --acknowledge)"
            r = dict(r, orphaned_ledger_ids=growth)
            ack_n = min(orphaned, ack)
        if raw and orphaned:
            print(f"  ack        {r['space']}: {ack_n} orphaned ledger ids acknowledged "
                  f"({baseline.get('_acknowledged', '?')}); growth {growth} {how}")
        if r["duplicates"] or r["orphaned_ledger_ids"]:
            out.append(r)
    return out


def acknowledged_baseline(existing, findings, *, scanned, today):
    """The baseline after acknowledging the spaces scanned in THIS run.

    One baseline file serves the whole machine, and a machine can hold spaces
    under more than one root (plur-claw: ~/Data and ~/spaces), each scanned by
    its own run. So acknowledging replaces only the scanned spaces' entries and
    keeps every other space's acknowledged ids. It used to rebuild the file
    from empty, and acknowledging the second root erased the first (found
    2026-09-23 on plur-claw). Legacy COUNT entries (ints) are dropped: an id
    baseline replaces them.
    """
    scanned = set(scanned)
    base = {k: v for k, v in (existing or {}).items()
            if isinstance(v, list) and k not in scanned}
    for r in findings:
        if r.get("orphaned_ledger_ids"):
            base[r["space"]] = r.get("orphaned_ids") or []
    base["_acknowledged"] = today
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--acknowledge", action="store_true",
                    help="record today's orphaned ids as the known baseline; "
                         "later runs report every id churned since (no noise floor)")
    args = ap.parse_args()

    spaces = sorted(args.root.glob("[0-9]-*"))
    # SCANNING NOTHING IS NOT A PASS. Pointed at the wrong root — a second
    # checkout, a moved file, a bad --root — this finds zero spaces, finds zero
    # findings in them, and exits 0. Every layer above reads that as healthy.
    # This detector's own first bug was exactly that shape.
    if not spaces:
        print(f"ERROR: no spaces under {args.root} — refusing to report clean")
        return 2
    baseline_path = Path.home() / ".datacore" / "state" / "id-churn.baseline.json"
    baseline = {} if args.acknowledge else _load_baseline(baseline_path)
    legacy = is_legacy_baseline(baseline)
    findings = [r for r in (scan_space(s, noise_floor=legacy) for s in spaces
                            if (s / "org").is_dir()) if r]
    # Acknowledged damage. On 2026-08-11 dedup regenerated 1,204 ids; the
    # ledger still references the old ones (360 in 0-personal, 271 in
    # 2-datacore on 2026-09-03). That is not repairable by this detector and
    # alerting on it every hour hid every NEW churn behind it. --acknowledge
    # records the orphaned ids (unfloored); from then on every id outside that
    # set is a finding, and the acknowledged amount is printed so it is never
    # invisible.
    if args.acknowledge:
        base = acknowledged_baseline(_load_baseline(baseline_path), findings,
                                     scanned=[s.name for s in spaces],
                                     today=datetime.date.today().isoformat())
        baseline_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = baseline_path.with_suffix(".json.tmp"); tmp.write_text(json.dumps(base, indent=1)); tmp.replace(baseline_path)
        print("acknowledged orphaned ledger ids as baseline: "
              + ", ".join(f"{k}={len(v)}" for k, v in base.items() if isinstance(v, list)))
        return 0
    if legacy:
        print(f"  note       legacy COUNT baseline ({baseline.get('_acknowledged', '?')}): "
              f"churn under {int(NOISE_FLOOR * 100)}% of open ledger ids is not reported; "
              f"re-run --acknowledge to record ids", file=sys.stderr)
    findings = apply_baseline(findings, baseline)

    if args.json:
        print(json.dumps({"findings": findings, "spaces": len(spaces)}, indent=2))
    else:
        for r in findings:
            if r["duplicates"]:
                print(f"  DUPLICATES {r['space']}: {r['duplicates']} "
                      f"(e.g. {', '.join(r['examples'])}) — fix BEFORE dedup_ids fires")
            if r["orphaned_ledger_ids"]:
                print(f"  CHURNED    {r['space']}: {r['orphaned_ledger_ids']} ledger ids "
                      f"no longer present in org")
        print(f"\nid-churn: {len(spaces)} space(s), {len(findings)} with findings")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
