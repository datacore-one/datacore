#!/usr/bin/env python3
"""attestation_coverage.py — report how much of the ledger is actually attested.

WHY. DIP-0034 (Event Ledger Substrate) is marked Implemented and declares
`<root>/.datacore/keys/registry.yaml` as tracked. Measured 2026-09-02:

    registry.yaml            does not exist on any host
    events in root ledger    6,351
    events carrying a sig    0
    callers passing strict   none

`verify_chain` checks a signature only `if event.sig != ""`, and treats an
unsigned event as an error only when `strict=True`, which nothing sets. So the
chain's hash and sequence integrity IS verified and working, while actor
attestation is verified for exactly nothing — and returns a clean pass.

That is bug class 3 inside the ledger that the class-3 work was built on: the
absence of a check is indistinguishable from the check succeeding.

This tool does not sign anything and does not create keys. Minting signing
keys and distributing them across hosts changes the fleet's security posture
and is the owner's decision, not a repair a tool should make quietly. What it
does is make the gap legible, so "attestation is off" is a number on a
dashboard rather than a silence.

    attestation_coverage.py            # all ledgers under DATACORE_ROOT
    attestation_coverage.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(os.environ.get("DATACORE_ROOT", pathlib.Path.home() / "Data"))
REGISTRY = ROOT / ".datacore" / "keys" / "registry.yaml"
SIGN_FLAG = "DATACORE_LEDGER_SIGN"


def _ledgers() -> list[pathlib.Path]:
    out = list((ROOT / ".datacore" / "events").glob("*.jsonl"))
    for space in ROOT.glob("[0-9]-*"):
        out += list((space / ".datacore" / "events").glob("*.jsonl"))
    return sorted(out)


def measure() -> dict:
    known_actors = 0
    if REGISTRY.exists():
        try:
            import yaml
            data = yaml.safe_load(REGISTRY.read_text()) or {}
            known_actors = len((data.get("actors") or {}))
        except Exception:
            known_actors = 0

    per_log, signed, unsigned, malformed = [], 0, 0, 0
    for f in _ledgers():
        s = u = 0
        for line in f.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if e.get("sig"):
                s += 1
            else:
                u += 1
        signed += s
        unsigned += u
        per_log.append({"log": str(f.relative_to(ROOT)), "signed": s, "unsigned": u})

    total = signed + unsigned
    return {
        "signing_enabled": os.environ.get(SIGN_FLAG) == "1",
        "registry_path": str(REGISTRY),
        "registry_exists": REGISTRY.exists(),
        "known_actors": known_actors,
        "logs": len(per_log),
        "events": total,
        "signed": signed,
        "unsigned": unsigned,
        "malformed_lines": malformed,
        "coverage_pct": round(100.0 * signed / total, 2) if total else 0.0,
        "attestation_active": REGISTRY.exists() and known_actors > 0 and signed > 0,
        "per_log": per_log,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    m = measure()

    if a.json:
        json.dump(m, sys.stdout, indent=1)
        return 0

    print(f"key registry     {m['registry_path']}")
    print(f"                 {'present' if m['registry_exists'] else 'ABSENT'}"
          f"  ({m['known_actors']} known actor(s))")
    print(f"ledgers          {m['logs']}")
    print(f"events           {m['events']}")
    print(f"  signed         {m['signed']}")
    print(f"  unsigned       {m['unsigned']}")
    if m["malformed_lines"]:
        print(f"  malformed      {m['malformed_lines']}")
    print(f"coverage         {m['coverage_pct']}%")
    print()
    if not m["attestation_active"]:
        print("ATTESTATION IS NOT ACTIVE — and this is a switch, not missing work.")
        print(f"  {SIGN_FLAG} is "
              f"{'set' if m['signing_enabled'] else 'unset'}.")
        print()
        print("  Signing is opt-in by design; ledger/policy.py says the ledger")
        print("  'becomes cryptographic only when DATACORE_LEDGER_SIGN=1'. With it")
        print("  unset, EventLog never calls ensure_keypair, so no keys and no")
        print("  registry exist and every event carries sig=\"\". That is why")
        print("  registry.yaml is absent: it is generated on the first signed")
        print("  write, not a file anyone forgot.")
        print()
        print("  What is NOT by design is the silence. verify_chain checks a")
        print("  signature only when one is present, and flags an unsigned event")
        print("  only under strict=True, which no production caller sets — so a")
        print("  wholly unattested chain returns a clean pass.")
        print()
        print("  Verified end-to-end 2026-09-02: with the flag set, events sign,")
        print("  the registry is created with this actor, and verify_chain passes")
        print("  under strict=True. Enabling it is a fleet-wide posture change and")
        print("  is the owner's call — but it is a decision, not a build.")
        return 1
    print("attestation active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
