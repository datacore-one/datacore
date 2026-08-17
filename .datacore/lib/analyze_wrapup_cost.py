#!/usr/bin/env python3
"""
analyze_wrapup_cost.py — measure what /wrap-up actually costs, per session.

Reads Claude Code transcripts, finds the turn where /wrap-up was invoked, and
sums everything after it: tokens, turns, wall-clock, subagent spawns. The
pre-wrap-up part of the same session is measured too, so the answer is a
RATIO ("wrap-up was 38% of the session") and not just a scary absolute number.

Motivated by ENG-2026-0505-029 (wrap-up token cost was Fermi-estimated 5000x
too low) — never estimate this, read the usage objects.

Usage:
  python3 .datacore/lib/analyze_wrapup_cost.py                    # all sessions
  python3 .datacore/lib/analyze_wrapup_cost.py --since 2026-07-01
  python3 .datacore/lib/analyze_wrapup_cost.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from glob import glob
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# A wrap-up starts at the user turn that invokes it. Slash-command invocations
# arrive as <command-name>wrap-up</command-name>; natural-language triggers are
# listed in the command's own Usage section.
WRAPUP_MARKERS = (
    "<command-name>/wrap-up",
    "<command-name>wrap-up",
    "/wrap-up",
)


def _text(msg) -> str:
    """Flatten a message's content to searchable text."""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(
                b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
            )
    return ""


def _ts(rec) -> datetime | None:
    t = rec.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def analyze(path: Path) -> dict | None:
    records = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not records:
        return None

    # Locate the wrap-up boundary: the first user turn that invokes it.
    boundary = None
    for i, rec in enumerate(records):
        if rec.get("type") != "user" or rec.get("isSidechain"):
            continue
        body = _text(rec.get("message", {}))
        if any(m in body for m in WRAPUP_MARKERS):
            boundary = i
            break
    if boundary is None:
        return None

    def tally(slice_):
        out = {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0, "turns": 0, "agents": 0}
        for rec in slice_:
            if rec.get("type") != "assistant":
                continue
            u = (rec.get("message") or {}).get("usage") or {}
            out["in"] += u.get("input_tokens", 0)
            out["cache_w"] += u.get("cache_creation_input_tokens", 0)
            out["cache_r"] += u.get("cache_read_input_tokens", 0)
            out["out"] += u.get("output_tokens", 0)
            out["turns"] += 1
            for blk in (rec.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    if blk.get("name") in ("Task", "Agent"):
                        out["agents"] += 1
        return out

    before, after = tally(records[:boundary]), tally(records[boundary:])

    stamps = [t for t in (_ts(r) for r in records[boundary:]) if t]
    minutes = (max(stamps) - min(stamps)).total_seconds() / 60 if len(stamps) > 1 else 0.0
    day = None
    for rec in records[boundary:]:
        t = _ts(rec)
        if t:
            day = t.date().isoformat()
            break

    # Billable = uncached input + cache writes + output. Cache reads are ~10x
    # cheaper and would otherwise swamp the comparison.
    bill = lambda d: d["in"] + d["cache_w"] + d["out"]  # noqa: E731
    return {
        "session": path.stem,
        "date": day,
        "wrapup_minutes": round(minutes, 1),
        "wrapup_turns": after["turns"],
        "wrapup_agents": after["agents"],
        "wrapup_output_tokens": after["out"],
        "wrapup_billable_tokens": bill(after),
        "session_billable_tokens": bill(before) + bill(after),
        "wrapup_share_pct": (
            round(100 * bill(after) / (bill(before) + bill(after)), 1)
            if (bill(before) + bill(after))
            else 0.0
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="-Users-gregor-Data")
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = []
    for p in glob(str(PROJECTS_DIR / args.project / "*.jsonl")):
        try:
            r = analyze(Path(p))
        except Exception:  # a malformed transcript must not kill the sweep
            continue
        if r and r["date"] and (not args.since or r["date"] >= args.since):
            rows.append(r)
    rows.sort(key=lambda r: r["date"])

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"{'date':11} {'min':>6} {'turns':>6} {'agents':>7} {'out_tok':>9} {'bill_tok':>10} {'share':>6}")
    for r in rows:
        print(
            f"{r['date']:11} {r['wrapup_minutes']:6.1f} {r['wrapup_turns']:6d} "
            f"{r['wrapup_agents']:7d} {r['wrapup_output_tokens']:9,d} "
            f"{r['wrapup_billable_tokens']:10,d} {r['wrapup_share_pct']:5.1f}%"
        )
    if rows:
        n = len(rows)
        print(f"\n{n} wrap-ups measured")
        for label, key in (
            ("minutes", "wrapup_minutes"),
            ("turns", "wrapup_turns"),
            ("agents", "wrapup_agents"),
            ("billable tokens", "wrapup_billable_tokens"),
            ("share of session", "wrapup_share_pct"),
        ):
            vals = sorted(r[key] for r in rows)
            print(f"  median {label:17} {vals[n // 2]:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
