#!/usr/bin/env python3
"""Is every hub space settled? The hub's seal-age job contract (plan 2026-10-31, phase D).

For each space under the root that has opted in to a hub (ledger-hub.json),
the newest seal must be:

  * under one hour old (the hub seals every 15 minutes; DATACORE_HUB_SEAL_INTERVAL_S),
    or, when older, current: it covers every admitted event. The hub writes no
    seal when nothing changed (`Hub.seal` answers `unchanged`), so a quiet
    space keeps an old seal; it fails only when admitted work waits unsealed
    past the hour. (A stopped hub on a space where nothing arrives is not
    visible from seals alone -- and has nothing to settle.)
  * made by the space's sequencer: the active grantor the owner's records name,
    which must also be the `grantor` its ledger-hub.json names (Winston), and
  * verified over admitted events (`ledger.hub.seal_report`, the same judgement
    as `ledger_seal.py status`).

    ledger_seal_age_check.py [--root ~/Data] [--max-age-s 3600]

Output: one indented line per hub space, then one verdict line, LAST:
`OK <n> hub space(s) ...` only when every hub space passes, else `FAIL ...`, or
`NONE ...` when no space has a hub (could-not-tell, never a pass). The job
contract (box-ledger-seal-age in jobs/manifest.yaml) reads that last line.
Exit 0 on OK, 1 on FAIL, 2 on NONE. Reads only; writes nothing.

Why: the hub process had no job contract, so no checker watched that it keeps
running and sealing (processes page, 2026-10-31). A hub that stops leaves every
reader on its git copy and nothing settles; this says so within the hour.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger import hub as hubmod  # noqa: E402

MAX_AGE_S = 3600


def check_space(space_dir, *, now_ms: int | None = None, max_age_s: int = MAX_AGE_S, expected: str | None = None,
                registry_path=None) -> dict:
    """{space, ok, detail, sealer, age_s} for one hub space."""
    space = Path(space_dir)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    out = {"space": space.name, "ok": False, "sealer": None, "age_s": None}
    try:
        cfg = hubmod.load_config(space)
        if cfg is None:
            return {**out, "detail": "not a hub space"}
        expected = expected or cfg.grantor
        r = hubmod.seal_report(space, registry_path=registry_path, now_ms=now)
    except Exception as exc:  # noqa: BLE001 -- one unreadable space is a FAIL line, never a crash
        return {**out, "detail": f"could not judge: {type(exc).__name__}: {exc}"}
    seal = r["seal"]
    out["sealer"] = r["sealer"]
    if seal is None:
        return {**out, "detail": f"no seal by the grantor {r['sealer']} over admitted events"}
    sealed_ms = hubmod._physical_ms(seal.hlc)
    age_s = None if sealed_ms is None else max(0, now - sealed_ms) // 1000
    out["age_s"] = age_s
    problems = []
    if r["sealer"] != expected or seal.sequencer != expected:
        problems.append(f"sealed by {seal.sequencer} (active grantor {r['sealer']}), not {expected} "
                        "as ledger-hub.json names")
    if age_s is None:
        problems.append(f"the newest seal's time cannot be read ({seal.hlc!r})")
    elif age_s >= max_age_s and r.get("current") is not True:
        problems.append(f"the newest seal is {age_s // 60} min old (limit {max_age_s // 60} min) and admitted "
                        "events wait unsealed: the hub has stopped sealing")
    if r["ok"] is not True:
        problems.append(f"the newest seal does not verify: {r['detail']}")
    if problems:
        return {**out, "detail": "; ".join(problems)}
    quiet = "" if age_s < max_age_s else ", nothing admitted since (the hub writes no seal when nothing changed)"
    return {**out, "ok": True, "detail": f"sealed {age_s // 60} min ago by {seal.sequencer}, verifies{quiet}"}


def hub_spaces(root: Path) -> list[Path]:
    out = []
    for p in sorted(Path(root).glob("[0-9]-*")):
        try:
            if p.is_dir() and hubmod.load_config(p) is not None:
                out.append(p)
        except Exception:  # noqa: BLE001 -- an unreadable config is judged (and failed) below
            out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data"))))
    ap.add_argument("--max-age-s", type=int, default=MAX_AGE_S)
    ap.add_argument("--expect", default=None, help="the sealer every space must name (default: its ledger-hub.json grantor)")
    ap.add_argument("--registry", type=Path, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    spaces = hub_spaces(a.root.expanduser())
    if not spaces:
        print(f"NONE no space under {a.root} has a hub -- nothing to judge, not a pass")
        return 2
    now = int(time.time() * 1000)
    results = [check_space(s, now_ms=now, max_age_s=a.max_age_s, expected=a.expect, registry_path=a.registry)
               for s in spaces]
    for r in results:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['space']:<14} {r['detail']}")
    bad = [r["space"] for r in results if not r["ok"]]
    if bad:
        print(f"FAIL {len(bad)} of {len(results)} hub space(s) not freshly sealed by their grantor: {', '.join(bad)}")
        return 1
    print(f"OK {len(results)} hub space(s): newest seal under {a.max_age_s // 60} min, by the grantor, verifies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
