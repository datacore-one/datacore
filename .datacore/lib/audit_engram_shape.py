#!/usr/bin/env python3
"""
audit_engram_shape.py — audit engram statement shape in the PLUR store.

Read-only. Never writes to the store.

Two questions, one tool:

  1. ATOMICITY. How many engrams hold several independent claims at once?
     Compound statements cannot be meaningfully decayed, confidence-scored,
     or contradicted, because the score applies to the whole blob. They also
     resist rewriting into second-person voice for resident injection.

  2. SHAPE BY DOMAIN. Which domains produce compound statements, and how
     large is the "voice" slice (unconditional style/tone/interaction rules
     that apply to every reply regardless of topic)?

Resistance is scored by counting independent compound signals:
  words > 80, enumerations >= 2, ALLCAPS section labels >= 1,
  sentences >= 5, semicolons >= 3, technical tokens (IP/path/URL) >= 3.
A statement scoring >= 2 is treated as resistant, >= 3 as severe.

Companion note: audit_recall_coverage.py audits `recall:` frontmatter in
commands and agents. This audits the statements inside the store itself.

Usage:
  python3 .datacore/lib/audit_engram_shape.py                 # full report
  python3 .datacore/lib/audit_engram_shape.py --json          # machine output
  python3 .datacore/lib/audit_engram_shape.py --resistant     # list offenders
  python3 .datacore/lib/audit_engram_shape.py --voice         # size voice slice
  python3 .datacore/lib/audit_engram_shape.py --domain plur   # filter by prefix
  python3 .datacore/lib/audit_engram_shape.py --strict        # exit 1 over budget

Exit codes:
  0  report produced, or resistant share within budget
  1  resistant share exceeds --budget (only when --strict is passed)
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

PLUR_HOME = Path.home() / ".plur"

ENUM = re.compile(r"\(\d\)")
SECTION = re.compile(r"\b[A-Z][A-Z /-]{3,}:")
IPADDR = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
PATHISH = re.compile(r"(?:/[\w.\-]+){2,}")
URL = re.compile(r"https?://")
SENTENCE = re.compile(r"[.!?](?:\s|$)")

# Domain prefixes that carry unconditional voice rules (style, tone,
# interaction) rather than task facts. Over-selects by roughly half:
# operational statements get filed under comms.* and collaboration.*
# regularly, so treat the result as a candidate list needing a human pass.
VOICE_PREFIXES = (
    "writing",
    "communication",
    "preferences",
    "collaboration",
    "style",
    "comms.engagement",
    "personal.learning",
)

DEFAULT_BUDGET = 10.0  # percent resistant tolerated before --strict fails


def discover_stores(home: Path) -> list[tuple[str, Path]]:
    stores: list[tuple[str, Path]] = []
    personal = home / "engrams.yaml"
    if personal.exists():
        stores.append(("personal", personal))
    for pack in sorted((home / "packs").glob("*/engrams.yaml")):
        stores.append((f"pack:{pack.parent.name}", pack))
    return stores


def load_store(path: Path) -> list[dict]:
    with open(path) as fh:
        data = yaml.safe_load(fh)
    rows = data.get("engrams") if isinstance(data, dict) else data
    return [e for e in (rows or []) if isinstance(e, dict)]


def signals(text: str) -> dict:
    return {
        "words": len(text.split()),
        "enum": len(ENUM.findall(text)),
        "section": len(SECTION.findall(text)),
        "sentences": len(SENTENCE.findall(text)),
        "semicolons": text.count(";"),
        "tech": len(IPADDR.findall(text)) + len(PATHISH.findall(text)) + len(URL.findall(text)),
    }


def resistance(sig: dict) -> int:
    return sum([
        sig["words"] > 80,
        sig["enum"] >= 2,
        sig["section"] >= 1,
        sig["sentences"] >= 5,
        sig["semicolons"] >= 3,
        sig["tech"] >= 3,
    ])


def collect(home: Path, domain_filter: str | None) -> list[dict]:
    rows = []
    for origin, path in discover_stores(home):
        for e in load_store(path):
            stmt = (e.get("statement") or "").strip()
            if not stmt:
                continue
            domain = e.get("domain") or ""
            if domain_filter and not domain.startswith(domain_filter):
                continue
            sig = signals(stmt)
            rows.append({
                "id": e.get("id", "?"),
                "origin": origin,
                "status": e.get("status", "?"),
                "type": e.get("type", "?"),
                "domain": domain,
                "scope": e.get("scope", ""),
                "signals": sig,
                "resistance": resistance(sig),
                "statement": stmt,
            })
    return rows


def summarise(rows: list[dict]) -> dict:
    active = [r for r in rows if r["status"] == "active"]
    if not active:
        return {"active": 0}

    words = sorted(r["signals"]["words"] for r in active)

    def pct(p: int) -> int:
        return words[min(int(len(words) * p / 100), len(words) - 1)]

    by_origin = defaultdict(lambda: {"n": 0, "resistant": 0})
    for r in active:
        bucket = by_origin[r["origin"]]
        bucket["n"] += 1
        bucket["resistant"] += r["resistance"] >= 2

    by_domain = defaultdict(list)
    for r in active:
        by_domain[r["domain"].split(".")[0] or "(none)"].append(r)

    domains = {}
    for prefix, group in by_domain.items():
        domains[prefix] = {
            "n": len(group),
            "median_words": statistics.median(x["signals"]["words"] for x in group),
            "resistant_pct": 100 * sum(1 for x in group if x["resistance"] >= 2) / len(group),
            "types": dict(Counter(x["type"] for x in group).most_common(3)),
        }

    resistant = [r for r in active if r["resistance"] >= 2]
    return {
        "active": len(active),
        "no_domain": sum(1 for r in active if not r["domain"]),
        "words": {
            "median": pct(50), "p75": pct(75), "p90": pct(90),
            "p95": pct(95), "p99": pct(99), "max": words[-1],
            "mean": round(statistics.mean(words), 1),
        },
        "resistance_hist": dict(sorted(Counter(r["resistance"] for r in active).items())),
        "resistant": len(resistant),
        "resistant_pct": round(100 * len(resistant) / len(active), 1),
        "severe": sum(1 for r in active if r["resistance"] >= 3),
        "by_origin": {k: dict(v) for k, v in by_origin.items()},
        "by_domain": domains,
    }


def voice_slice(rows: list[dict]) -> dict:
    active = [r for r in rows if r["status"] == "active" and r["origin"] == "personal"]
    picked = [r for r in active if r["domain"].startswith(VOICE_PREFIXES)]
    words = [r["signals"]["words"] for r in picked]
    return {
        "population": len(active),
        "candidates": len(picked),
        "share_pct": round(100 * len(picked) / len(active), 2) if active else 0.0,
        "median_words": statistics.median(words) if words else 0,
        "total_words": sum(words),
        "approx_tokens": int(sum(words) * 1.4),
        "domains": dict(Counter(r["domain"] for r in picked).most_common()),
        "ids": [r["id"] for r in picked],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit engram statement shape (read-only).")
    ap.add_argument("--home", type=Path, default=PLUR_HOME, help="PLUR home (default ~/.plur)")
    ap.add_argument("--domain", help="only engrams whose domain starts with this prefix")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--resistant", action="store_true", help="list resistant engrams")
    ap.add_argument("--voice", action="store_true", help="size the voice slice")
    ap.add_argument("--limit", type=int, default=20, help="rows for --resistant (default 20)")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET,
                    help=f"max resistant %% before --strict fails (default {DEFAULT_BUDGET})")
    ap.add_argument("--strict", action="store_true", help="exit 1 if over budget")
    args = ap.parse_args()

    if not args.home.exists():
        print(f"no PLUR store at {args.home}", file=sys.stderr)
        return 2

    rows = collect(args.home, args.domain)
    if not rows:
        print("no engrams matched", file=sys.stderr)
        return 2

    report = summarise(rows)
    voice = voice_slice(rows) if args.voice else None
    offenders = sorted(
        (r for r in rows if r["status"] == "active" and r["resistance"] >= 2),
        key=lambda r: (-r["resistance"], -r["signals"]["words"]),
    )[:args.limit] if args.resistant else None

    if args.json:
        payload = {"summary": report}
        if voice:
            payload["voice"] = voice
        if offenders is not None:
            payload["resistant"] = [
                {k: r[k] for k in ("id", "domain", "type", "resistance", "signals")}
                for r in offenders
            ]
        print(json.dumps(payload, indent=2, default=str))
    else:
        w = report["words"]
        print(f"active engrams   : {report['active']}")
        print(f"missing domain   : {report['no_domain']} "
              f"({100 * report['no_domain'] / report['active']:.1f}%)")
        print(f"words            : median {w['median']}  p90 {w['p90']}  "
              f"p99 {w['p99']}  max {w['max']}")
        print(f"resistant (>=2)  : {report['resistant']} ({report['resistant_pct']}%)")
        print(f"severe    (>=3)  : {report['severe']}")
        print()
        print("BY ORIGIN")
        for origin, b in sorted(report["by_origin"].items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {origin:30s} {b['n']:5d}  {100 * b['resistant'] / b['n']:5.1f}% resistant")
        print()
        print(f"  {'DOMAIN':26s} {'n':>5s} {'medwords':>9s} {'resist%':>8s}")
        for prefix, d in sorted(report["by_domain"].items(), key=lambda kv: -kv[1]["n"]):
            if d["n"] < 15:
                continue
            print(f"  {prefix:26s} {d['n']:5d} {d['median_words']:9.0f} {d['resistant_pct']:7.1f}%")
        if voice:
            print()
            print(f"VOICE SLICE      : {voice['candidates']} candidates "
                  f"({voice['share_pct']}% of personal), ~{voice['approx_tokens']} tokens")
            print("  NOTE: domain over-selects. Expect roughly half to be operational")
            print("        statements filed under a voice domain. Human pass required.")
        if offenders:
            print()
            print("RESISTANT")
            for r in offenders:
                s = r["signals"]
                print(f"  {r['id']:26s} {r['domain'][:28]:28s} w={s['words']:4d} "
                      f"enum={s['enum']} sect={s['section']} sent={s['sentences']} "
                      f"tech={s['tech']} res={r['resistance']}")

    if args.strict and report["resistant_pct"] > args.budget:
        print(f"\nover budget: {report['resistant_pct']}% > {args.budget}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
