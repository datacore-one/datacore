#!/usr/bin/env python3
"""How a visitor joins the network: measure, converge, and say so once.

A laptop is not a server that is sometimes off. It is a node whose presence is
not promised (jobs.awake.node_class == "visitor"): it arrives when a person opens
the lid, contributes while they work, and leaves without notice. For a month the
monitoring around it pretended otherwise, and every detector grew its own patch
for the lid -- awake-time ages, a dark-wake hold, a grace period moved to the
right clock. This is the one mechanism those patches were each approximating.

It is anti-entropy on arrival, which is what a Dynamo or Cassandra replica does
when it rejoins, and the vocabulary fits exactly: every writer's log carries a
monotonic `seq`, so the set of (writer -> highest seq) IS a version vector, and
detectors/seq_gap.py is already a version-vector diff. A join is:

    1. MEASURE divergence in both directions, BEFORE syncing.
       The order is the whole point. seq_gap exists to catch a machine hoarding
       work nobody else can see -- 610 commits once sat on a parked branch for
       two months. Sync first and that question is always answered "no", because
       you have just repaired what it was looking for: the detector would be
       deleted while its alert stayed. So what was pending is recorded first.
    2. CONVERGE: the ordinary phase-1 cycle -- fetch, merge (never rebase),
       publish, ingest, regenerate the projections a person reads.
    3. MEASURE again.
    4. Write ONE artifact, atomically, and only when the join converged.

THE ONE ALARM. `join.json` is rewritten only by a successful join, so its age --
judged in AWAKE time, like every freshness bound on this machine -- is exactly
"how long has this laptop been present without converging". A failed join on a
train is not an incident and writes nothing over the last good record; a laptop
that has been open for eight waking hours and still cannot converge is one, and
the contract says so on those grounds and no others. Everything else about a
visitor is silence.

A closed lid is a LEAVE, not a failure. Nothing here runs in a dark wake.

    visitor_join.py --tick      what launchd calls every few minutes
    visitor_join.py --now       join regardless of whether one is due
    visitor_join.py --status    print the last record
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
STATE = Path(os.environ.get("DATACORE_STATE", str(Path.home() / ".datacore" / "state")))
RECORD = STATE / "join.json"
ATTEMPT = STATE / "join-attempt.json"
LOG = STATE / "join.log"

#: A join is also due when the last good one is this old in AWAKE hours, so a
#: laptop left open for days keeps converging instead of only doing so on wake.
REFRESH_AWAKE_H = 4.0
#: Never attempt more often than this. A lid flapping on a bad network must not
#: turn into a git operation across ten repositories every few minutes.
MIN_SPACING_S = 600


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, payload: dict) -> None:
    """A reader sees the previous complete record or the new one, never half."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def measure(*, fetch: bool, now_ms: float | None = None, sleep_log: str | None = None) -> dict:
    """The version-vector diff, both ways, across every space.

    ahead   events this machine wrote that the remote does not have
    behind  events the remote has that this machine does not
    gap     the part of `ahead` that has outlived the publisher's grace
    """
    from detectors import seq_gap

    ahead = behind = gap = 0
    blocked: list[str] = []
    errors: list[str] = []
    for space in sorted(ROOT.glob("[0-9]-*")):
        if not (space / ".datacore" / "events").is_dir() or not (space / ".git").exists():
            continue
        for row in seq_gap.scan_space(space, fetch=fetch, now_ms=now_ms, sleep_log=sleep_log):
            if row.get("error"):
                errors.append(f"{row['space']}/{row['actor']}: {row['error']}")
                continue
            local, remote = row.get("local_seq"), row.get("remote_seq")
            ahead += int(row.get("gap") or 0) + int(row.get("pending") or 0)
            gap += int(row.get("gap") or 0)
            if isinstance(local, int) and isinstance(remote, int) and remote > local:
                behind += remote - local
            if row.get("gap") and row.get("why"):
                blocked.append(f"{row['space']}/{row['actor']}: {row['why']}")
    return {"ahead": ahead, "behind": behind, "gap": gap,
            "blocked": blocked[:6], "errors": errors[:6]}


def converge() -> tuple[int, str]:
    """The ordinary phase-1 cycle. It takes its own lock, so a join at wake and
    the hourly schedule cannot both be ingesting."""
    script = LIB / "ledger_phase1_cycle.sh"
    try:
        proc = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                              timeout=1500, env={**os.environ, "DATACORE_ROOT": str(ROOT)})
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, f"{type(exc).__name__}: {exc}"
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    return proc.returncode, tail[0][:200]


def join(*, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    before = measure(fetch=True)
    rc, detail = converge()
    after = measure(fetch=False)
    converged = rc == 0 and after["gap"] == 0 and not after["errors"]

    record = {
        "joined_at": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "converged": converged,
        # What was pending when this machine arrived -- the hoarding evidence,
        # taken before the sync that would have erased it.
        "ahead_by": before["ahead"],
        "behind_by": before["behind"],
        "still_ahead": after["ahead"],
        "still_unpublished_past_grace": after["gap"],
        "cycle_rc": rc,
        "cycle": detail,
        "blocked": after["blocked"],
        "errors": after["errors"],
    }
    _write_atomic(ATTEMPT, {"at": now, "ok": converged})
    LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(f"{record['iso']} converged={converged} ahead_by={before['ahead']} "
                 f"behind_by={before['behind']} still_gap={after['gap']} rc={rc}"
                 + (f" blocked={after['blocked'][0]}" if after["blocked"] else "") + "\n")
    if converged:
        # ONLY a successful join replaces the record. A run that learned nothing
        # -- no network, a held publisher -- must not overwrite the last one that
        # did; the record's age is the alarm, and rewriting it on failure would
        # reset the very clock that is meant to be running.
        _write_atomic(RECORD, record)
    return record


def due(*, now: float | None = None, log: str | None = None) -> tuple[bool, str]:
    """Should this tick join? (yes/no, why) -- pure, so it can be tested."""
    from jobs import awake

    now = time.time() if now is None else now
    if awake.in_dark_wake(log=log):
        return False, "dark wake: the lid is shut, nobody has arrived"
    attempt = _load(ATTEMPT)
    if attempt and now - float(attempt.get("at", 0)) < MIN_SPACING_S:
        return False, "attempted within the last ten minutes"
    last = float(_load(RECORD).get("joined_at") or 0)
    if not last:
        return True, "no join on record"
    wake = awake.last_full_wake(log=log)
    if wake and wake > last:
        return True, "woke since the last join"
    if attempt and not attempt.get("ok"):
        return True, "the last attempt did not converge"
    from actor_identity import this_actor
    if awake.awake_age(last, this_actor(), now=now, log=log) > REFRESH_AWAKE_H * 3600:
        return True, f"last join is over {REFRESH_AWAKE_H:g} waking hours old"
    return False, "joined since waking"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if a.status:
        print(json.dumps(_load(RECORD) or {"joined": None}, indent=2))
        return 0
    if a.tick:
        ok, why = due()
        if not ok:
            print(f"join: not due — {why}")
            return 0
        print(f"join: due — {why}")
    elif not a.now:
        ap.print_help()
        return 0

    rec = join()
    print(f"join: converged={rec['converged']} ahead_by={rec['ahead_by']} "
          f"behind_by={rec['behind_by']} still_unpublished_past_grace="
          f"{rec['still_unpublished_past_grace']}"
          + (f"\n  blocked: {rec['blocked'][0]}" if rec["blocked"] else ""))
    # Not converging is a condition, not a crash: the contract on join.json's age
    # is what decides whether it has gone on long enough to matter.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
