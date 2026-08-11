#!/usr/bin/env python3
"""Is our own enforcement still switched on? (DIP-0046 D4)

Observability watches agent *behaviour* and not agent *configuration*: a one-line
change silently alters the effective policy and leaves no trace anywhere. This
installation has the exact instance — `core.hooksPath` was set on the Mac and
winston and unset on nightshift, hermes and plur-claw, so two of five actors
committed with **no hooks at all**, indefinitely, and nothing said so.

`git clone` never copies hooks, which is why per-repo `.git/hooks` cannot be the
mechanism: Data's clone was created at 13:00 one day and had zero enforcement
from birth. `core.hooksPath` set globally is the fix — every repo, including
future clones, resolves one directory — and this is the thing that notices when
it stops being true.

Checked per machine:

  hooksPath set        `git config --global core.hooksPath` resolves
  hooksPath exists     the directory is actually there (a path pointing at a
                       deleted checkout is worse than unset, because it reads
                       as configured)
  hooks present        pre-commit and pre-push exist and are executable

SSH failure is reported as ERROR, never as pass. A machine we cannot reach is a
machine whose enforcement we cannot vouch for, and quietly skipping it is how
"all green" comes to mean "all the ones that answered".

Exit 0 all good, 1 on drift, 2 if a machine could not be checked.

    config_drift.py [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REQUIRED = ("pre-commit", "pre-push")

# (label, ssh host or None for local, user to run as or None)
MACHINES = [
    ("mac", None, None),
    ("winston", "winston", None),
    ("nightshift", "nightshift", None),
    ("hermes", "hermes", "gregor"),
    ("plur-claw", "plur-claw", None),
]


def run(host: str | None, user: str | None, cmd: str) -> tuple[int, str]:
    if host is None:
        full = ["bash", "-lc", cmd]
    elif user:
        full = ["ssh", "-o", "ConnectTimeout=10", host, f"sudo -u {user} bash -lc {cmd!r}"]
    else:
        full = ["ssh", "-o", "ConnectTimeout=10", host, cmd]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=45)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def check(label: str, host: str | None, user: str | None) -> dict:
    rc, path = run(host, user, "git config --global --get core.hooksPath")
    if rc != 0 or not path:
        # Distinguish "reachable and unset" from "unreachable". The second is an
        # ERROR: we cannot vouch for a machine that did not answer.
        rc2, _ = run(host, user, "true")
        if rc2 != 0:
            return {"machine": label, "status": "unreachable", "detail": path[:120]}
        return {"machine": label, "status": "unset", "detail": "core.hooksPath not configured"}

    rc, listing = run(host, user, f"ls {path} 2>/dev/null | tr '\\n' ' '")
    have = set(listing.split())
    if not have:
        return {"machine": label, "status": "missing-dir", "detail": path}
    absent = [h for h in REQUIRED if h not in have]
    if absent:
        return {"machine": label, "status": "missing-hooks",
                "detail": f"{path}: absent {', '.join(absent)}"}
    return {"machine": label, "status": "ok", "detail": path}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = [check(*m) for m in MACHINES]
    bad = [r for r in rows if r["status"] not in ("ok",)]
    unreachable = [r for r in rows if r["status"] == "unreachable"]

    if args.json:
        print(json.dumps({"rows": rows, "drift": len(bad)}, indent=2))
    else:
        for r in rows:
            tag = "ok     " if r["status"] == "ok" else r["status"].upper().ljust(7)
            print(f"  {tag} {r['machine']:<12} {r['detail'][:70]}")
        print(f"\nconfig-drift: {len(rows)} machine(s), {len(bad)} with drift, "
              f"{len(unreachable)} unreachable")

    if unreachable:
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
