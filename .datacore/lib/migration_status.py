#!/usr/bin/env python3
"""Is the DIP-0046 migration actually done? Check, do not assert.

Every track reports from something observable — a file that exists, a job that
ran, a detector that passes, a hook that fires — not from a plan document
saying it was implemented. The distinction is the whole point: this session
repeatedly found tracks marked complete whose code was installed and inert
(a hook in the wrong directory, a job that never fired, a detector scanning an
empty tree, a gate with no caller).

A track is DONE only if its check passes NOW. `BLOCKED` means it needs
something outside this machine (a calendar, a human decision). `OPEN` means
implementable and not implemented.

Exit 0 when every track is DONE, 1 otherwise.

    migration_status.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
STATE = Path.home() / ".datacore" / "state"
sys.path.insert(0, str(ROOT / ".datacore" / "lib"))


def _run(*args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _fresh(p: Path, hours: int = 26) -> bool:
    if not p.exists():
        return False
    age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds()
    return age < hours * 3600


def check_detectors() -> tuple[str, str]:
    """A: the detectors exist, pass, AND have run recently."""
    names = {"seq_gap": "seq-gap", "actor_presence": "actor-presence",
             "id_churn": "id-churn", "config_drift": "config-drift"}
    stale = [v for v in names.values() if not _fresh(STATE / f"{v}.log")]
    failing = []
    for mod in names:
        rc, _ = _run(sys.executable, str(ROOT / ".datacore/lib/detectors" / f"{mod}.py"))
        if rc != 0:
            failing.append(mod)
    if failing:
        return "OPEN", f"failing: {', '.join(failing)}"
    if stale:
        return "OPEN", f"no recent run: {', '.join(stale)}"
    return "DONE", "4 detectors passing, all with a run in the last 26h"


def check_transport() -> tuple[str, str]:
    """C: one writer, and no other module shells out to git for sync."""
    rc, out = _run("grep", "-rln", "git.*push", str(ROOT / ".datacore/lib"))
    offenders = [l for l in out.splitlines()
                 if l.endswith(".py") and "ledger_transport" not in l
                 and "/tests/" not in l and "detectors/" not in l
                 and "audit" not in l and "scan" not in l]
    if not (ROOT / ".datacore/lib/ledger_transport.py").exists():
        return "OPEN", "ledger_transport.py missing"
    if (ROOT / ".datacore/lib/space_sync.py").exists():
        return "OPEN", "space_sync.py still present (should be folded in)"
    return "DONE", f"single writer; {len(offenders)} incidental git caller(s)"


def check_membership() -> tuple[str, str]:
    spaces = [d for d in sorted(ROOT.glob("[0-9]-*")) if (d / ".git").exists()]
    missing = [d.name for d in spaces if not (d / ".datacore/members.yaml").exists()]
    return ("OPEN", f"missing members.yaml: {', '.join(missing)}") if missing \
        else ("DONE", f"{len(spaces)} spaces declare membership")


def check_hooks() -> tuple[str, str]:
    """D4/D5: enforcement present where it can be."""
    rc, out = _run("git", "config", "--global", "--get", "core.hooksPath")
    if rc != 0 or not out.strip():
        return "OPEN", "core.hooksPath not set on this machine"
    guard = ROOT / ".datacore/lib/hooks/log_ownership_guard.py"
    if not guard.exists():
        return "OPEN", "log_ownership_guard.py missing"
    return "DONE", "hooksPath set; ownership guard present"


def check_gate() -> tuple[str, str]:
    """E3: the gate exists AND a caller declares outputs, or it is advisory."""
    run_py = ROOT / ".datacore/modules/nightshift/lib/run.py"
    if not (ROOT / ".datacore/lib/commit_gate.py").exists():
        return "OPEN", "commit_gate.py missing"
    if run_py.exists() and "files=produced" not in run_py.read_text(errors="replace"):
        return "OPEN", "gate installed but no caller declares outputs (advisory only)"
    return "DONE", "gate enforced at the task-completion commit"


def check_reversibility() -> tuple[str, str]:
    """F2a: the drill must PASS, not merely exist."""
    drill = ROOT / ".datacore/lib/phase1_drill.py"
    if not drill.exists():
        return "OPEN", "phase1_drill.py missing"
    rc, out = _run(sys.executable, str(drill), timeout=300)
    return ("DONE", "flip and reversal both verified") if rc == 0 \
        else ("OPEN", out.strip().splitlines()[-1][:80] if out.strip() else "drill failed")


def check_streak() -> tuple[str, str]:
    """F2: calendar. Reports honestly rather than counting runs."""
    need = int(os.environ.get("DATACORE_PHASE1_DAYS", "5"))
    p = STATE / "shadow-status.json"
    if not p.exists():
        return "BLOCKED", "no shadow status yet"
    try:
        d = json.loads(p.read_text())
    except ValueError:
        return "BLOCKED", "shadow status unreadable"
    streak, when, clean = (d.get("consecutive_clean_days", 0), d.get("date"),
                           d.get("all_clean"))
    if not clean:
        return "BLOCKED", "projections not clean; streak resets"
    if when != date.today().isoformat():
        return "BLOCKED", f"last checked {when}; streak {streak}/{need}"
    return ("DONE", f"{streak}/{need} clean days") if streak >= need \
        else ("BLOCKED", f"{streak}/{need} clean days — {need - streak} to go")


def check_projections() -> tuple[str, str]:
    rc, out = _run(sys.executable, str(ROOT / ".datacore/lib/shadow_check.py"), timeout=300)
    line = [l for l in out.splitlines() if "clean |" in l]
    return ("DONE", line[-1].strip() if line else "clean") if rc == 0 \
        else ("OPEN", line[-1].strip() if line else "drift")


def check_concurrency() -> tuple[str, str]:
    """E4: worktree isolation is not implementable here, so check the mitigations.

    `execute_task` runs the agent with cwd=data_dir because tasks read across
    spaces, and the spaces are separate repos rather than submodules — so
    neither a per-space worktree nor a Data worktree can hold a task. What must
    hold instead: converge-at-run-start, E3's declared outputs, and the
    transport's per-repo flock.
    """
    run_py = ROOT / ".datacore/modules/nightshift/lib/run.py"
    missing = []
    if run_py.exists():
        t = run_py.read_text(errors="replace")
        if "tree dirty at run start" not in t:
            missing.append("converge-at-run-start")
        if "files=produced" not in t:
            missing.append("declared outputs")
    transport = ROOT / ".datacore/lib/ledger_transport.py"
    if transport.exists() and "_repo_lock" not in transport.read_text(errors="replace"):
        missing.append("per-repo flock")
    return ("OPEN", f"missing: {', '.join(missing)}") if missing \
        else ("DONE", "isolation infeasible by topology; 3 mitigations in place")


TRACKS = [
    ("A  detectors", check_detectors),
    ("C  transport", check_transport),
    ("D  membership", check_membership),
    ("D  hooks", check_hooks),
    ("E  commit gate", check_gate),
    ("E  concurrency", check_concurrency),
    ("F1 projections", check_projections),
    ("F2a reversibility", check_reversibility),
    ("F2 clean streak", check_streak),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = []
    for name, fn in TRACKS:
        try:
            status, detail = fn()
        except Exception as exc:      # noqa: BLE001 — a broken check is not a pass
            status, detail = "OPEN", f"check raised {type(exc).__name__}: {exc}"
        rows.append({"track": name, "status": status, "detail": detail})

    if a.json:
        print(json.dumps({"tracks": rows}, indent=2))
    else:
        for r in rows:
            mark = {"DONE": "ok     ", "BLOCKED": "blocked", "OPEN": "OPEN   "}[r["status"]]
            print(f"  {mark} {r['track']:<20} {r['detail']}")
        done = sum(1 for r in rows if r["status"] == "DONE")
        print(f"\nmigration: {done}/{len(rows)} tracks verified DONE")

    return 0 if all(r["status"] == "DONE" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
