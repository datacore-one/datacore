#!/usr/bin/env python3
"""Decision board for Datacore's behaviour spec: the PROMISES the system makes
to its owner, grouped by capability, in plain language.

Evals-first (owner, 2026-09-26): the owner reviews what Datacore promises
before any eval is written. Each promise then gets evals behind it, and the
same list becomes the regression scoreboard. Reads every `promises/*.yaml`
under a folder (schema: capabilities[key, name, what_it_does, promises[id,
promise, today, evidence, existing_evals, question?]]).

Per promise: right as written / change it (say how in the note) / not needed.
Per capability, one extra row: is anything missing? Saving downloads
<slug>.decisions.json; nothing is applied from here.

Usage:
    promise_board.py --folder 2-datacore/1-tracks/dev/datacore-upgrade
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_catalog_board import write_board  # noqa: E402

STATUS = {"holds": "Works today", "broken": "Broken today", "unknown": "Not checked yet"}
OPTIONS = [
    {"value": "right", "label": "Right", "consequence": "Evals are written behind it as it stands."},
    {"value": "change", "label": "Change it", "consequence": "Say how in the note; evals follow your wording."},
    {"value": "drop", "label": "Not needed", "consequence": "Datacore will not promise this."},
]
MISSING = [
    {"value": "complete", "label": "Nothing missing", "consequence": "This capability's list is complete."},
    {"value": "add", "label": "Add something", "consequence": "Write the missing promise in the note."},
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", required=True, type=Path, help="folder holding promises/*.yaml")
    ap.add_argument("--slug", default="datacore-promises")
    ap.add_argument("--title", default="What Datacore promises")
    args = ap.parse_args()

    files = sorted((args.folder / "promises").glob("*.yaml"))
    sections, counts = [], {"holds": 0, "broken": 0, "unknown": 0}
    for f in files:
        for cap in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("capabilities") or []:
            rows = []
            for p in cap.get("promises") or []:
                today = str(p.get("today") or "unknown")
                counts[today] = counts.get(today, 0) + 1
                rows.append({
                    "id": str(p["id"]),
                    "area": STATUS.get(today, today),
                    "title": str(p["promise"]).strip(),
                    "context": str(p.get("question") or ""),
                    "options": OPTIONS,
                    "suggested": "right",
                })
            order = {"Broken today": 0, "Not checked yet": 1, "Works today": 2}
            rows.sort(key=lambda r: (order.get(r["area"], 3), r["id"]))
            key = str(cap["key"])
            rows.append({
                "id": f"{rows[0]['id'].split('-')[0]}-MISSING" if rows else f"{key}-MISSING",
                "area": "Anything missing?",
                "title": f"Is there anything else {cap['name']} should always do, or never do?",
                "context": "Think of what you rely on it for, and what would hurt if it broke.",
                "options": MISSING,
                "suggested": "complete",
            })
            sections.append({"key": key, "label": str(cap["name"]), "hint": str(cap.get("what_it_does") or ""),
                             "rows": rows, "noted": []})

    total = sum(counts.values())
    meta = {
        "slug": args.slug, "title": args.title,
        "eyebrow": "Evals first · step 1 of 3",
        "h1": args.title,
        "lede": ("Everything Datacore should always do for you, written as promises. Mark each one "
                 "right, change it, or drop it, and add what is missing. Every promise you keep gets "
                 "tests behind it, and this same list becomes the daily check that nothing regressed."),
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": [f"{len(sections)} capabilities"],
        "path": [["Promises", f"{total} in {len(sections)} capabilities"],
                 ["Today", f"{counts.get('holds', 0)} work · {counts.get('broken', 0)} broken · "
                           f"{counts.get('unknown', 0)} not checked"],
                 ["Your review", "on this page"],
                 ["Then", "tests behind each promise, red first"]],
    }
    data = {"meta": meta, "sections": sections, "prefill": {}}
    meta["build"] = hashlib.sha256(json.dumps(sections, sort_keys=True, ensure_ascii=False)
                                   .encode()).hexdigest()
    out = write_board(data, args.title, args.slug)
    print(json.dumps({"out": str(out), "promises": total, "capabilities": len(sections),
                      "today": counts}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
