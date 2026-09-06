#!/usr/bin/env python3
"""Prompt versions tied to outcomes (product description, stage 7).

Every completion an executor records carries, since 2026-09-06, the agent
definition, its registry version, the model and the auth path, and the
evaluators' consensus score. This folds those events across every space
into one table per (agent, version, model): how many, mean score, share of
proposals, last seen. The weekly contract writes it to a log the briefing
can quote; a version whose mean drops is visible the week it drops.

    agent_outcomes.py [--root DIR] [--days N] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def outcomes(root: Path = ROOT, days: int = 90, now: float | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc).timestamp()
    cutoff = now - days * 86400
    acc: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "scored": 0, "score_sum": 0.0, "proposals": 0, "last": 0.0, "spaces": set()})
    for f in glob.glob(str(root / "[0-9]-*" / ".datacore" / "events" / "*.jsonl")):
        space = Path(f).parts[-4]
        for line in Path(f).read_text(errors="replace").splitlines():
            if '"item.complete"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "item.complete":
                continue
            ms = str(e.get("hlc", "")).split(".")[0]
            t = int(ms) / 1000 if ms.isdigit() else 0
            if t < cutoff:
                continue
            p = e.get("payload") or {}
            key = (str(p.get("agent") or e.get("actor") or "?"), str(p.get("agent_version") or ""), str(p.get("model") or ""))
            a = acc[key]
            a["n"] += 1; a["last"] = max(a["last"], t); a["spaces"].add(space)
            if isinstance(p.get("score"), (int, float)):
                a["scored"] += 1; a["score_sum"] += float(p["score"])
            if p.get("decision") == "proposal":
                a["proposals"] += 1
    rows = []
    for (agent, ver, model), a in sorted(acc.items(), key=lambda kv: -kv[1]["n"]):
        rows.append({"agent": agent, "version": ver, "model": model, "completions": a["n"],
                     "mean_score": round(a["score_sum"] / a["scored"], 3) if a["scored"] else None,
                     "scored": a["scored"], "proposals": a["proposals"],
                     "last": dt.datetime.fromtimestamp(a["last"], dt.timezone.utc).date().isoformat() if a["last"] else "",
                     "spaces": sorted(a["spaces"])})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = outcomes(a.root, a.days)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    if a.json:
        print(json.dumps(rows, indent=2)); return 0
    print(f"{stamp} agent-outcomes: {len(rows)} (agent, version, model) row(s) over {a.days} days")
    for r in rows:
        ms = f"{r['mean_score']:.2f}" if r["mean_score"] is not None else "  --"
        print(f"  {r['agent']:14} v{r['version'] or '-':6} {r['model'] or '-':22} n={r['completions']:4} score={ms} proposals={r['proposals']:3} last={r['last']} spaces={','.join(r['spaces'])[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
