#!/usr/bin/env python3
"""Rehearsal for the Gitea pre-receive hook (DIP-0046 D5).

Each case pushes for real into a throwaway bare repo and asserts on what the
server did, because the two bugs this found were both invisible to a unit test:

  1. A global `core.hooksPath` SILENTLY DISABLES per-repo server-side hooks.
     The hook file was installed, executable, and correct — and never ran. Any
     check of the form "is the hook present" reads green through this. The only
     honest check is whether a push that should be refused actually is, which is
     what these cases do. (Hence `--receive-pack` pinning hooksPath below: the
     Mac running this test has a global one.)

  2. Enforce mode rejected a space that merely had no members.yaml yet. The
     comment said "report, never reject"; the code appended to `violations`.
     `0-personal` is the operator's daily space and a pre-receive rejection
     cannot be bypassed from the client, so that bug is a lockout.

Run: python3 .datacore/lib/tests/test_gitea_pre_receive.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "gitea-pre-receive.py"
# The bare repo must resolve ITS OWN hooks/ — see failure 1 above.
RECEIVE_PACK = "git -c core.hooksPath=hooks receive-pack"
ZEROISH = "members:\n  - mac\n  - winston\n"


def sh(cwd: Path, *args: str, env: dict | None = None) -> tuple[int, str]:
    e = {**os.environ, **(env or {})}
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=e)
    return r.returncode, r.stdout + r.stderr


def setup(tmp: Path) -> tuple[Path, Path]:
    srv, wt = tmp / "srv.git", tmp / "wt"
    sh(tmp, "git", "init", "-q", "--bare", str(srv))
    shutil.copy(HOOK, srv / "hooks" / "pre-receive")
    (srv / "hooks" / "pre-receive").chmod(0o755)
    sh(tmp, "git", "init", "-q", str(wt))
    sh(wt, "git", "config", "user.email", "t@t")
    sh(wt, "git", "config", "user.name", "t")
    (wt / ".datacore" / "events").mkdir(parents=True)
    (wt / ".datacore" / "members.yaml").write_text(ZEROISH)
    (wt / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":1}\n')
    sh(wt, "git", "add", "-A")
    sh(wt, "git", "commit", "-qm", "init")
    sh(wt, "git", "remote", "add", "origin", str(srv))
    return srv, wt


def push(wt: Path, actor: str, enforce: bool = False) -> tuple[int, str]:
    env = {"DATACORE_ACTOR": actor}
    if enforce:
        env["DATACORE_ENFORCE"] = "1"
    return sh(wt, "git", "push", f"--receive-pack={RECEIVE_PACK}",
              "origin", "main", env=env)


def head(srv: Path) -> str:
    return sh(srv, "git", "rev-parse", "refs/heads/main")[1].strip()


def main() -> int:
    fails = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if not cond else ""))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        srv, wt = setup(tmp)

        rc, out = push(wt, "mac")
        check("member pushing own log is silent", rc == 0 and "datacore/" not in out, out[-200:])

        (wt / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":2}\n')
        sh(wt, "git", "commit", "-qam", "t2")
        rc, out = push(wt, "data")
        check("non-member is reported", "not in .datacore/members.yaml" in out, out[-200:])
        check("report-only still accepts the push", rc == 0, out[-200:])

        (wt / ".datacore" / "events" / "winston.jsonl").write_text('{"seq":9}\n')
        sh(wt, "git", "add", "-A")
        sh(wt, "git", "commit", "-qm", "t3")
        rc, out = push(wt, "mac")
        check("cross-actor log write is reported", "single-writer" in out, out[-200:])

        # Enforce must refuse AND leave the ref where it was. Printing a refusal
        # while the ref advances is the failure mode that matters.
        before = head(srv)
        (wt / ".datacore" / "events" / "winston.jsonl").write_text('{"seq":10}\n')
        sh(wt, "git", "commit", "-qam", "t4")
        rc, out = push(wt, "data", enforce=True)
        check("enforce refuses", rc != 0 and "REJECT" in out, out[-200:])
        check("enforce leaves the ref unmoved", head(srv) == before,
              f"{before[:7]} -> {head(srv)[:7]}")

        # The lockout case.
        sh(wt, "git", "rm", "-q", ".datacore/members.yaml")
        (wt / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":3}\n')
        sh(wt, "git", "commit", "-qam", "t5")
        rc, out = push(wt, "mac", enforce=True)
        check("unmigrated space is NOT rejected under enforce", rc == 0, out[-300:])
        check("unmigrated space still warns", "unmigrated space" in out, out[-200:])

    print(f"\npre-receive rehearsal: {'FAILED ' + ', '.join(fails) if fails else 'all cases pass'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
