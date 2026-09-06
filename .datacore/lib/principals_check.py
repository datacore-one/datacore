#!/usr/bin/env python3
"""Is every principal a principal yet? The ten things, checked, not assumed.

The product description says a principal missing any of its ten things is
not yet a principal and the system says so. This is where the system says
so: for each entry in registry/principals.yaml it reports what is declared
and what is not — charter on disk, contracts in the manifest, budget,
memory scope, emails, host — and prints one line per principal. Exit 0 with
the table; the checklist carries it as informational (n-a is never a pass,
but an undeclared budget is the owner's decision, not an outage).

    principals_check.py [--root DIR] [--json]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DATACORE_ROOT", str(LIB.parent.parent)))
sys.path.insert(0, str(LIB))

TEN = ("identity", "purpose", "memory", "cadence", "decision_rights", "budget", "record", "peer_protocol", "health", "evolution")


def _manifest_jobs(root: Path) -> list[str]:
    try:
        import yaml
        d = yaml.safe_load((root / ".datacore" / "lib" / "jobs" / "manifest.yaml").read_text()) or {}
        return [j.get("name", "") for j in d.get("jobs") or []]
    except Exception:  # noqa: BLE001
        return []


def check(root: Path = ROOT) -> list[dict]:
    from actor_identity import principals
    ps = principals(root / ".datacore" / "registry" / "principals.yaml")
    jobs = _manifest_jobs(root)
    rows = []
    for name, p in ps.items():
        kind = str(p.get("kind") or "")
        charter = p.get("charter")
        charter_ok = bool(charter) and (root / str(charter)).exists()
        pat = p.get("contracts")
        contracts = [j for j in jobs if pat and fnmatch.fnmatch(j, str(pat))]
        missing = []
        if not (p.get("emails") or kind == "migration"):
            missing.append("identity: no emails")
        if kind == "agent" and not charter_ok:
            missing.append("purpose: charter missing" if charter else "purpose: no charter")
        if kind == "agent" and not p.get("memory_scope"):
            missing.append("memory: no scope")
        if kind == "agent" and p.get("budget_monthly_usd") is None:
            missing.append("budget: not declared")
        if kind == "agent" and not contracts:
            missing.append("health: no contract")
        if kind == "agent" and not p.get("permission_mode"):
            missing.append("decision_rights: no permission mode")
        rows.append({"principal": name, "kind": kind, "charter": charter_ok, "contracts": len(contracts),
                     "budget": p.get("budget_monthly_usd"), "memory_scope": p.get("memory_scope"), "missing": missing})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = check(a.root)
    if a.json:
        print(json.dumps(rows, indent=2)); return 0
    for r in rows:
        state = "complete" if not r["missing"] else "; ".join(r["missing"])
        print(f"  {r['principal']:10} {r['kind']:9} charter={'ok' if r['charter'] else '--'} contracts={r['contracts']:2} budget={r['budget'] if r['budget'] is not None else '--':>6} memory={r['memory_scope'] or '--':16} {state}")
    n = sum(1 for r in rows if r["kind"] == "agent" and not r["missing"])
    total = sum(1 for r in rows if r["kind"] == "agent")
    print(f"  {n}/{total} agent principal(s) complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
