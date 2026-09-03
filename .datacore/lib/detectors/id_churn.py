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

Exit 0 clean, 1 on duplicates or correspondence loss, 2 on error.

    id_churn.py [--root DIR] [--json]
"""
from __future__ import annotations

import argparse
import os
import datetime
import json
import re
from collections import Counter
from pathlib import Path

ID_RE = re.compile(r":ID:\s*(\S+)")
ORG_FILES = ("next_actions.org", "inbox.org")


def scan_space(space: Path) -> dict | None:
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
        # Ledger ids with no org task. A handful is normal drift (a task was
        # completed and archived); a large fraction is the churn signature.
        orphaned = len(led - org_ids)
        if led and orphaned / len(led) < 0.25:
            orphaned = 0        # below the noise floor: ordinary lifecycle
    if not dupes and not orphaned:
        return None
    return {"space": space.name, "duplicates": sum(dupes.values()),
            "examples": sorted(dupes)[:3], "orphaned_ledger_ids": orphaned}


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


def apply_baseline(findings: list, baseline: dict) -> list:
    """Drop orphaned counts at or below the acknowledged baseline; keep
    duplicates always (they are the trigger, never acknowledged)."""
    out = []
    for r in findings:
        ack = int(baseline.get(r["space"], 0) or 0)
        orphaned = int(r.get("orphaned_ledger_ids") or 0)
        growth = max(0, orphaned - ack)
        if ack and orphaned:
            print(f"  ack        {r['space']}: {min(orphaned, ack)} orphaned ledger ids acknowledged "
                  f"({baseline.get('_acknowledged', '?')}); growth {growth}")
        r = dict(r, orphaned_ledger_ids=growth)
        if r["duplicates"] or r["orphaned_ledger_ids"]:
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--acknowledge", action="store_true",
                    help="record today\'s orphaned-id counts as the known baseline; "
                         "later runs report only GROWTH above it")
    args = ap.parse_args()

    spaces = sorted(args.root.glob("[0-9]-*"))
    # SCANNING NOTHING IS NOT A PASS. Pointed at the wrong root — a second
    # checkout, a moved file, a bad --root — this finds zero spaces, finds zero
    # findings in them, and exits 0. Every layer above reads that as healthy.
    # This detector's own first bug was exactly that shape.
    if not spaces:
        print(f"ERROR: no spaces under {args.root} — refusing to report clean")
        return 2
    findings = [r for r in (scan_space(s) for s in spaces if (s / "org").is_dir()) if r]
    # Acknowledged damage. On 2026-08-11 dedup regenerated 1,204 ids; the
    # ledger still references the old ones (360 in 0-personal, 271 in
    # 2-datacore on 2026-09-03). That is not repairable by this detector and
    # alerting on it every hour hid every NEW churn behind it. --acknowledge
    # records the counts; from then on only growth above them is a finding,
    # and the acknowledged amount is printed so it is never invisible.
    baseline_path = Path.home() / ".datacore" / "state" / "id-churn.baseline.json"
    if args.acknowledge:
        base = {r["space"]: r["orphaned_ledger_ids"] for r in findings if r["orphaned_ledger_ids"]}
        base["_acknowledged"] = datetime.date.today().isoformat()
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = baseline_path.with_suffix(".json.tmp"); tmp.write_text(json.dumps(base, indent=1)); tmp.replace(baseline_path)
        print(f"acknowledged orphaned ledger ids as baseline: {base}")
        return 0
    findings = apply_baseline(findings, _load_baseline(baseline_path))

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
