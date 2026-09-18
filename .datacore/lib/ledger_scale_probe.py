#!/usr/bin/env python3
"""Step 5 of 5: what does this cost at 2x and 5x the ledger it has today?

Creating ONE delegated item took 3m12s on 2026-09-18. Two fixes brought it to
about 14s -- memoizing a registry that was being re-parsed once per event, and
stopping `latest_jobs` from chain-verifying all 68 writer logs to answer a
question about one principal's freshness. Both were found because a person
happened to wait for a command and thought "that is slow", which is not a
control.

The reason it matters more here than in most systems: the ledger is APPEND
ONLY. Every cost that scales with the number of events is a cost that only ever
grows, and the cheapest moment to see the curve is before it hurts. 67,610
events today; this asks what the same operations cost at twice and five times
that, by folding synthetic events into a scratch root rather than by waiting
eighteen months.

It measures the operations on the hot path of ordinary use:

  fold          read + fold one space (every dispatcher, every status call)
  create        the full gated item.create, which is what got slow
  absence       claim_gate.absent, which reads attestations fleet-wide
  invariants    the step-3 sweep, which reads every event by definition

A budget is asserted so this can run unattended: `--max-create-seconds` fails
the probe when a gated create at 5x exceeds it. The default is deliberately
loose -- this is a cliff detector, not a benchmark, and a noisy performance
alarm gets muted faster than any other kind.

    ledger_scale_probe.py [--multipliers 1 2 5] [--max-create-seconds N] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

#: Loose on purpose. A gated create is interactive-adjacent (it happens when a
#: task is captured), and anything past this is a cliff rather than a wobble.
DEFAULT_CREATE_BUDGET_S = 20.0


def _synthesize(space: Path, actor: str, count: int) -> None:
    """Append `count` real events through the real writer, no shortcuts.

    Hand-writing JSONL would measure a different system: the hash chain, the
    seq high-water mark and the fsync are all part of what append costs.
    """
    from ledger.log import EventLog
    log = EventLog(space, actor, sign=False)
    for n in range(count):
        log.append("item.create", {"id": f"scale-{actor}-{n}", "title": f"synthetic {n}",
                                   "state": "TODO", "space": space.name})


def _isolate(root: Path) -> None:
    """Give the scratch root its own identity and policy.

    Without this the probe inherits the real installation's registry -- the
    resolver deliberately falls back to it -- and the synthetic writer is
    refused as unregistered. A measurement that silently runs against the
    operator's own config is measuring the wrong machine anyway.
    """
    registry = root / ".datacore" / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "principals.yaml").write_text(
        "principals:\n  scale: {kind: agent, writes_as: [scale]}\n")
    config = root / ".datacore" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "approvals_policy.yaml").write_text(
        "version: 1\napprover: human\ncosign_effects: [payment]\n"
        # The synthetic writer needs headroom the real ones must not have: the
        # daily creation cap exists to stop a runaway agent, and the probe IS a
        # runaway agent by construction.
        "principals:\n  scale: {may_delegate_to: [scale], max_creates_per_day: 10000000}\n")
    import actor_identity
    import ledger.policy as policy
    actor_identity.PRINCIPALS = registry / "principals.yaml"
    actor_identity.INFRA = registry / "infrastructure.yaml"
    actor_identity._PRINCIPALS_CACHE.clear()
    policy.DEFAULT_POLICY_PATH = config / "approvals_policy.yaml"


def measure(root: Path, events: int) -> dict:
    from ledger.fold import fold
    from ledger.log import EventLog, read_events
    from ledger.policy import guarded_append

    space = root / "1-scale"
    space.mkdir(parents=True, exist_ok=True)
    have = len(read_events(space)) if (space / ".datacore" / "events").is_dir() else 0
    if events > have:
        _synthesize(space, "scale", events - have)

    t = time.monotonic()
    folded = fold(read_events(space))
    fold_s = time.monotonic() - t

    t = time.monotonic()
    guarded_append(EventLog(space, "scale", sign=False), "item.create",
                   {"id": f"probe-{events}", "title": "the measured create",
                    "assignee": "scale"})
    create_s = time.monotonic() - t

    t = time.monotonic()
    try:
        from claim_gate import absent
        absent("scale", root=root)
    except Exception:  # noqa: BLE001 -- an unmeasurable step is not a failed probe
        pass
    absence_s = time.monotonic() - t

    t = time.monotonic()
    try:
        import ledger_invariants
        ledger_invariants.sweep(root, quick=False)
    except Exception:  # noqa: BLE001
        pass
    sweep_s = time.monotonic() - t

    return {"events": len(read_events(space)), "items": len(folded.items),
            "fold_s": round(fold_s, 3), "create_s": round(create_s, 3),
            "absence_s": round(absence_s, 3), "invariants_s": round(sweep_s, 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--multipliers", nargs="+", type=float, default=[1, 2, 5])
    ap.add_argument("--base", type=int, default=None,
                    help="events at 1x (default: this installation's largest space)")
    ap.add_argument("--max-create-seconds", type=float, default=DEFAULT_CREATE_BUDGET_S)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.base is None:
        live = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
        sizes = [sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
                 for p in live.glob("[0-9]-*/.datacore/events/*.jsonl")]
        a.base = max(sizes) if sizes else 1000

    base = tempfile.mkdtemp(prefix="scale-probe-")
    rows = []
    try:
        for mult in sorted(a.multipliers):
            # A fresh root per multiplier: reusing one would measure a warm
            # page cache and a warmed registry memo, which is not the question.
            root = Path(base) / f"x{mult:g}"
            root.mkdir(parents=True)
            os.environ["DATACORE_ROOT"] = str(root)
            _isolate(root)
            row = {"multiplier": mult, **measure(root, int(a.base * mult))}
            rows.append(row)
            print(f"  x{mult:<4g} {row['events']:>7} events  fold {row['fold_s']:>7.3f}s  "
                  f"create {row['create_s']:>7.3f}s  absence {row['absence_s']:>7.3f}s  "
                  f"invariants {row['invariants_s']:>7.3f}s")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    worst = max(r["create_s"] for r in rows)
    over = worst > a.max_create_seconds
    if a.json:
        print(json.dumps({"ok": not over, "base": a.base, "rows": rows,
                          "budget_s": a.max_create_seconds}, indent=2))
    else:
        print(f"\nledger-scale: {'OK' if not over else 'OVER BUDGET'} — "
              f"base {a.base} events/log, worst gated create {worst:.2f}s "
              f"of {a.max_create_seconds:.0f}s budget")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
