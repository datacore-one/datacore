#!/usr/bin/env python3
"""Turn a CoS briefing's `delegate` proposals into ledger items — the caller
`briefing.actions` never had.

DIP-0038 built the action loop and nothing invoked it: `materialize()` and
`act()` were complete, tested and unreferenced, so every briefing kept ending
in proposals with no mechanical way to act on them. That is the gap this
closes, and it is deliberately the SMALLEST caller that closes it: read the
artifact the briefing already produces, hand its `delegate` entries to
`materialize()`, print what happened.

What it does NOT do, on purpose:
  - It does not edit org files. Ledger items and GTD tasks are distinct item
    classes (DIP-0038, as amended after DIP-0034's 2026-08-04 boundary
    inversion), and `inbox.org` is never projected.
  - It does not run itself. Nothing calls this from the morning pipeline yet;
    wiring it into `cos_morning.sh` is a separate, owner-ratified step. Built
    as a seam, left dormant — the same shape as `DATACORE_LEDGER_SIGN`.

Two guarantees are worth stating because they are what make re-running safe:

  EXACTLY ONCE. Item ids are content hashes of the proposal text, so running
  this twice over the same briefing materializes nothing the second time. A
  briefing regenerated at 04:00 and again after a failed run does not produce
  duplicate items.

  NEVER SILENTLY UNGATED. A proposal carrying a side-effecting `effects` tag
  (email.send, payment, prod.deploy) with no recorded `approval.grant` is
  REFUSED by `guarded_append` and reported in `blocked` — it is never created
  as an ungated item. Verify this by causing it (see --demo-gate).

Usage:
    briefing_materialize.py --artifact PATH [--space DIR] [--actor NAME]
                            [--dry-run] [--demo-gate]

Exit codes: 0 nothing blocked; 1 one or more proposals were refused by policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing.actions import materialize  # noqa: E402

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def proposals(artifact: dict) -> list[dict]:
    """The briefing's `delegate` entries, as `materialize()` input.

    Each entry is `{task, why, agent_hint}`; `task` is the proposal text and
    therefore the dedupe key. `why` and `agent_hint` are context for a human
    reading the briefing, not part of the item's identity — folding them into
    the text would make an item's id change whenever the LLM rephrased its
    reasoning, which would defeat the exactly-once guarantee.

    No `effects` are inferred. An effect tag is what makes an item require a
    co-sign, so guessing one from prose would either gate work that needs no
    gate or, worse, fail to gate work that does. Effects must be declared
    explicitly upstream.
    """
    out = []
    for entry in artifact.get("delegate") or []:
        if isinstance(entry, dict) and (entry.get("task") or "").strip():
            item: dict = {"text": entry["task"].strip()}
            if entry.get("effects"):
                item["effects"] = entry["effects"]
            out.append(item)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact", required=True, type=Path)
    ap.add_argument("--space", type=Path, default=DATACORE_ROOT)
    ap.add_argument("--actor", default=os.environ.get("DATACORE_ACTOR", "cos"))
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be materialized; write nothing")
    ap.add_argument("--demo-gate", action="store_true",
                    help="append a synthetic side-effecting proposal to prove "
                         "the co-sign gate refuses it")
    args = ap.parse_args(argv)

    try:
        artifact = json.loads(args.artifact.read_text())
    except (OSError, ValueError) as exc:
        print(f"error: cannot read artifact {args.artifact}: {exc}", file=sys.stderr)
        return 2

    items = proposals(artifact)
    if args.demo_gate:
        items.append({
            "text": "DEMO co-sign gate: send an email nobody approved",
            "effects": ["email.send"],
        })

    if not items:
        print("no delegate proposals in this briefing")
        return 0

    if args.dry_run:
        for item in items:
            eff = f"  effects={item['effects']}" if item.get("effects") else ""
            print(f"would materialize: {item['text'][:90]}{eff}")
        return 0

    result = materialize(items, args.space, args.actor)
    for ev in result.created:
        print(f"created: {ev.payload['id']}  {ev.payload['title'][:80]}")
    for tid in result.skipped:
        print(f"skipped (already known): {tid}")
    for text, why in result.blocked:
        print(f"BLOCKED by policy: {text[:70]} -- {why}", file=sys.stderr)

    print(f"\n{len(result.created)} created, {len(result.skipped)} skipped, "
          f"{len(result.blocked)} blocked")
    return 1 if result.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
