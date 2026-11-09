#!/usr/bin/env python3
"""Decision board for an eval catalogue: the owner reviews each eval CLAIM
before any eval or code is written (evals-first, 2026-09-26).

Reads every `catalog/*.yaml` under a catalogue folder (schema: area, rows with
id, claim, kind, why, seeded_failure, existing_coverage, status_today,
priority) and renders the shared decision-board page (decision_board/board.css
and board.js, the same assets gtd_decision_board.py uses). Per row: keep,
reword (say how in the note), later, or drop. Saving downloads
<slug>.decisions.json; nothing is applied from here.

Usage:
    eval_catalog_board.py --catalog 2-datacore/1-tracks/dev/datacore-upgrade [--slug SLUG]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
ASSETS = LIB / "decision_board"

from file_utils import atomic_write_text, private_state_directory  # noqa: E402

KIND = {"D": "test", "F": "formal proof", "A": "agent behaviour", "P": "production check"}
TODAY = {"violated": "broken today", "holds": "holds today", "unknown": "not known today"}
PRIORITY = {"P1": "P1 · data loss, trust or outage", "P2": "P2 · correctness", "P3": "P3 · polish"}
OPTIONS = [
    {"value": "keep", "label": "Keep", "consequence": "Written as a failing eval, then made to pass."},
    {"value": "reword", "label": "Keep, reworded", "consequence": "Say how in the note; written from your wording."},
    {"value": "later", "label": "Later", "consequence": "Stays in the catalogue, not in this round."},
    {"value": "drop", "label": "Drop", "consequence": "Not a behaviour Datacore needs to guarantee."},
]


def _json_block(name: str, obj: object) -> str:
    s = json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f'<script type="application/json" id="{name}">{s}</script>'


def write_board(data: dict, title: str, slug: str) -> Path:
    """Render `data` on the shared decision-board page; owner-only, never published."""
    css = (ASSETS / "board.css").read_text()
    js = (ASSETS / "board.js").read_text()
    if "</script" in js.lower():
        raise SystemExit("board.js contains a literal </script")
    script_hash = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
    csp = (f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; "
           "base-uri 'none'; form-action 'none'")
    safe = title.replace("&", "&amp;").replace("<", "&lt;")
    page = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{safe}</title>'
            f"<style>{css}</style></head><body><div id=\"app\"></div>"
            f"{_json_block('data', data)}<script>{js}</script></body></html>\n")
    out = private_state_directory("decision-boards") / f"{datetime.now().date().isoformat()}-{slug}.html"
    atomic_write_text(out, page)
    return out


def rows_from(path: Path) -> tuple[str, list[dict]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    area = str(doc.get("area") or path.stem)
    out = []
    for r in doc.get("rows") or []:
        pr = str(r.get("priority") or "P3")
        cov = str(r.get("existing_coverage") or "none")
        out.append({
            "id": str(r["id"]),
            "area": PRIORITY.get(pr, pr),
            "title": str(r["claim"]).strip(),
            "context": f"Why: {str(r.get('why') or '').strip()}",
            "preview": f"Proven able to fail by: {str(r.get('seeded_failure') or '').strip()}  ·  "
                       f"Covered today: {cov}",
            "meta": [KIND.get(str(r.get("kind")), str(r.get("kind"))),
                     TODAY.get(str(r.get("status_today")), str(r.get("status_today")))],
            "options": OPTIONS,
            "suggested": "keep" if pr in ("P1", "P2") else "later",
            "_prio": pr,
        })
    out.sort(key=lambda r: (r["_prio"], r["id"]))
    for r in out:
        r.pop("_prio")
    return area, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", required=True, type=Path, help="folder holding catalog/*.yaml")
    ap.add_argument("--slug", default="eval-catalogue")
    ap.add_argument("--title", default="Datacore eval catalogue")
    args = ap.parse_args()

    labels = {"ledger-sync": "Ledger and sync", "gtd-tasks": "GTD and tasks",
              "alerts-messaging": "Alerts, messages and briefings",
              "agents-cadences": "Agents and cadences", "ops-install": "Operations and install"}
    sections, total, broken = [], 0, 0
    files = sorted((args.catalog / "catalog").glob("*.yaml"))
    for f in files:
        area, rows = rows_from(f)
        total += len(rows)
        broken += sum(1 for r in rows if "broken today" in r["meta"])
        sections.append({"key": area, "label": labels.get(area, area),
                         "hint": f"{len(rows)} claims, grouped by priority", "rows": rows, "noted": []})
    today = datetime.now()
    meta = {
        "slug": args.slug, "title": args.title,
        "eyebrow": "Evals first · review the claims before anything is written",
        "h1": args.title,
        "lede": ("Each row is one behaviour Datacore must guarantee, drawn from the last 30 days "
                 "of incidents and the ledger audit. Keep, reword, park or drop each one; kept "
                 "claims become failing evals first, then the code is fixed until they pass."),
        "asOf": today.strftime("%Y-%m-%d %H:%M"),
        "sources": [f.name for f in files],
        "path": [["Catalogued", f"{total} claims · {len(files)} areas"],
                 ["Broken today", f"{broken} claims"],
                 ["Your review", "decided on this page"],
                 ["Evals written", "red first, after your review"]],
    }
    data = {"meta": meta, "sections": sections, "prefill": {}}
    meta["build"] = hashlib.sha256(json.dumps(sections, sort_keys=True, ensure_ascii=False)
                                   .encode()).hexdigest()

    out = write_board(data, args.title, args.slug)
    print(json.dumps({"out": str(out), "claims": total, "broken_today": broken,
                      "areas": {s["label"]: len(s["rows"]) for s in sections}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
