#!/usr/bin/env python3
"""promise_fleet_board -- every machine's latest promise board, merged into one fleet board.

Each machine keeps its own nightly board (promise_nightly.py ->
~/.datacore/state/promise-scoreboard/latest.json). The fleet console read only
Miles's, and the other machines' boards reached the owner only through their
alerts (processes page, "The promise scoreboard", 2026-10-31). This merges the
latest board of every machine that has a `<machine>-promise-scoreboard` job
into one board, meant to run on the box after the last nightly board (04:30);
the fleet console (fleet_dashboard.py) reads it.

Per promise the fleet state is:
  red            red on any machine whose board is current (the machines are named);
  green          green on at least one current board and red on none;
  could-not-run  could not run on every current board.
A board older than --max-age-days (default 2) is listed as stale and a machine
whose board could not be read as missing; neither is counted -- an old green
says nothing about tonight.

    promise_fleet_board.py [--board MACHINE=PATH ...] [--out-dir DIR] [--max-age-days 2]

Without --board, each machine's latest.json is read through fleet_reach
(read-only `cat`). Writes fleet-<date>.json and fleet-latest.json atomically in
--out-dir (default: this machine's promise-scoreboard folder) and keeps the
last 14. Last line: `promise-fleet: <n> of <m> machine(s) merged ...`; exit 1
when no machine had a current board (nothing written), else 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from promise_nightly import CNR, GREEN, RED, STATE_DIR, _write  # noqa: E402

KEEP = 14
MAX_AGE_DAYS = 2
BOARD = "~/.datacore/state/promise-scoreboard/latest.json"


def board_machines(manifest: Path | None = None) -> list[str]:
    """Machines with a nightly promise board: a `*-promise-scoreboard` job in
    the job list this install runs (manifest.local.yaml laid over the tracked one)."""
    from jobs.manifest import effective_doc
    jobs = effective_doc(manifest or LIB / "jobs" / "manifest.yaml").get("jobs") or []
    return sorted({j["machine"] for j in jobs if isinstance(j, dict) and j.get("machine")
                   and str(j.get("name", "")).endswith("-promise-scoreboard")})


def merge(boards: dict, *, today: str, max_age_days: int = MAX_AGE_DAYS) -> dict:
    """The fleet board from {machine: board dict, or a reason string when it
    could not be read}."""
    machines, current = {}, {}
    for m, b in sorted(boards.items()):
        if not isinstance(b, dict) or not isinstance(b.get("promises"), dict):
            machines[m] = {"status": "missing", "why": b if isinstance(b, str) else "not a board"}
            continue
        d = str(b.get("date") or "")
        try:
            age = (_date.fromisoformat(today) - _date.fromisoformat(d)).days
        except ValueError:
            age = None
        info = {"date": d, "host": b.get("host"), "counts": b.get("counts") or {}}
        if age is None or age > max_age_days:
            machines[m] = {**info, "status": "stale"}
            continue
        machines[m] = {**info, "status": "merged"}
        current[m] = b["promises"]
    promises = {}
    for pid in sorted({p for ps in current.values() for p in ps}):
        per = {}
        for m, ps in current.items():
            e = ps.get(pid)
            if isinstance(e, dict):
                per[m] = {"state": e.get("state"), **({"why": e["why"]} if e.get("why") else {})}
        states = {v["state"] for v in per.values()}
        state = RED if RED in states else GREEN if GREEN in states else CNR
        text = next((ps[pid].get("text") for ps in current.values()
                     if isinstance(ps.get(pid), dict) and ps[pid].get("text")), "")
        promises[pid] = {"state": state, "text": text, "machines": per}
    counts: dict[str, int] = {}
    for e in promises.values():
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    return {"date": today, "kind": "fleet",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "merged": len(current), "machines": machines, "counts": counts, "promises": promises}


def fetch(machine: str):
    """That machine's latest board (read-only), or why it could not be read."""
    import fleet_reach
    try:
        pl = fleet_reach.place(machine)   # the box: its pod, where its jobs run since 2026-10-25
        r = fleet_reach.run_at(pl, f"cat {BOARD}", timeout=60)
    except Exception as exc:  # noqa: BLE001 -- an unreachable machine is a missing board, never a crash
        return f"unreachable: {type(exc).__name__}: {str(exc)[:120]}"
    if r.returncode != 0:
        return f"no board: {(r.stderr or '').strip()[:120] or 'rc ' + str(r.returncode)}"
    try:
        return json.loads(r.stdout)
    except ValueError:
        return "unreadable board (not JSON)"


def _read(path: str):
    try:
        return json.loads(Path(path).expanduser().read_text())
    except OSError as exc:
        return f"no board: {type(exc).__name__}"
    except ValueError:
        return "unreadable board (not JSON)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", action="append", default=[], metavar="MACHINE=PATH",
                    help="use this file as MACHINE's board instead of reading it from the machine")
    ap.add_argument("--out-dir", type=Path, default=STATE_DIR)
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    ap.add_argument("--today", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    today = a.today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if a.board:
        boards = {}
        for spec in a.board:
            m, sep, path = spec.partition("=")
            if not sep or not m:
                ap.error(f"--board needs MACHINE=PATH, got {spec!r}")
            boards[m] = _read(path)
    else:
        boards = {m: fetch(m) for m in board_machines()}
    fleet = merge(boards, today=today, max_age_days=a.max_age_days)
    for m, info in fleet["machines"].items():
        print(f"  {info['status']:<7} {m:<12} {info.get('date') or ''} {info.get('why') or ''}".rstrip())
    c = fleet["counts"]
    if not fleet["merged"]:
        print(f"promise-fleet: 0 of {len(boards)} machine(s) had a current board -- nothing written")
        return 1
    out = a.out_dir.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    _write(out / f"fleet-{today}.json", fleet)
    _write(out / "fleet-latest.json", fleet)
    for old in sorted(out.glob("fleet-2*.json"))[:-KEEP]:
        old.unlink(missing_ok=True)
    print(f"promise-fleet: {fleet['merged']} of {len(boards)} machine(s) merged; {c.get(GREEN, 0)} green, "
          f"{c.get(RED, 0)} red, {c.get(CNR, 0)} could not run anywhere -> {out / 'fleet-latest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
