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

#: This runner could not complete the call at all. Distinct from any exit status
#: the remote command itself can return, so a lost connection can never be read
#: as an answer. ssh reports its own connection failures as 255.
TRANSPORT = -1
SSH_FAILED = (TRANSPORT, 255)

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
        # NOT 1. Exit 1 is a real answer from `git config --get`: the key is not
        # set. Returning 1 for a timeout made "the call never completed" and
        # "the machine says it is unconfigured" the same number.
        return TRANSPORT, f"{type(exc).__name__}: {exc}"


def check(label: str, host: str | None, user: str | None, _retry: bool = True) -> dict:
    """One retry before declaring a host unreachable.

    The principle stands — a machine we cannot reach is a machine whose
    enforcement we cannot vouch for, and skipping it quietly is how "all green"
    comes to mean "all the ones that answered". But this runs from a LAPTOP on a
    6-hourly schedule, and a run that lands while the Mac is waking or the
    network has not settled reports a healthy fleet as unreachable. That fired
    at 06:50 on 2026-08-12 and passed on the very next manual run.
    
    A contract that cries wolf gets muted, and a muted contract is worth less
    than none. One retry separates "asleep for a moment" from "actually down";
    a machine that is genuinely gone fails both attempts and still reports.
    """
    rc, path = run(host, user, "git config --global --get core.hooksPath")
    if rc in SSH_FAILED or (rc != 0 and not path and rc != 1):
        # THE TRANSPORT FAILED, and that is never a verdict about configuration.
        if _retry and host is not None:
            import time
            time.sleep(5)
            return check(label, host, user, _retry=False)
        return {"machine": label, "status": "unreachable", "detail": path[:120]}
    if rc != 0 or not path:
        # `git config --get` exits 1 with no output when the key is unset --
        # that IS the machine answering. Confirm the host is up before believing
        # it, because a shell that mangles exit codes would otherwise turn a
        # dropped call into a finding. On 2026-09-18 at 14:54Z a laptop wake
        # produced exactly that: three hosts unreachable and plur-claw reported
        # "core.hooksPath not configured", which the next manual run showed
        # configured correctly. The mixed result also defeated the wake guard in
        # config_drift_run.sh, which holds back a run only when EVERY finding is
        # unreachable, so the false finding paged.
        rc2, _ = run(host, user, "true")
        if rc2 != 0:
            if _retry and host is not None:
                import time
                time.sleep(5)
                return check(label, host, user, _retry=False)
            return {"machine": label, "status": "unreachable", "detail": path[:120]}
        return {"machine": label, "status": "unset", "detail": "core.hooksPath not configured"}

    # The exit status of THIS call matters as much as the first one's. It used
    # to be discarded: `ls ... | tr` exits with tr's status, so a dropped ssh
    # connection and a missing directory both produced an empty listing, and
    # both were reported as "missing-dir". On 2026-09-17 one lost connection
    # during a 45-second maintenance wake reported nightshift's hooks directory
    # missing; it was there, tracked, and configured.
    #
    # Exit 3 is reserved for "the directory is not there". Any other failure is
    # the transport, and gets the same one retry as the first probe.
    rc, listing = run(host, user, f"test -d {path} || exit 3; ls {path} | tr '\\n' ' '")
    if rc == 3:
        return {"machine": label, "status": "missing-dir", "detail": path}
    if rc != 0:
        # Same rule as the first probe: exit 3 above is the only answer this
        # command gives about the directory; everything else is the transport.
        if _retry and host is not None:
            import time
            time.sleep(5)
            return check(label, host, user, _retry=False)
        return {"machine": label, "status": "unreachable", "detail": f"listing {path} failed"}
    have = set(listing.split())
    if not have:
        return {"machine": label, "status": "missing-dir", "detail": f"{path} (empty)"}
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
    unreachable = [r for r in rows if r["status"] == "unreachable"]
    # DRIFT IS WHAT WE SAW, NOT WHAT WE COULD NOT SEE. `bad` counted every
    # non-ok row, so two hosts a laptop could not reach were reported as "2
    # with drift, 2 unreachable" -- the same two machines counted as a
    # configuration fault and as a network condition at once. The log said
    # UNREACHABLE on both lines and the summary said drift, so the summary
    # disagreed with the body it was summarising, and the contract believed
    # the summary.
    #
    # They stay separate all the way to the exit code: unreachable already
    # returns 2 ("could not tell"), drift returns 1. Nothing here decides
    # whether a laptop off the network is worth waking someone for -- that is
    # the runner's judgement and the freshness bound's, and both need to be
    # told which of the two happened.
    bad = [r for r in rows if r["status"] not in ("ok", "unreachable")]

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
