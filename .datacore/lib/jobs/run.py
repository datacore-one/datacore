#!/usr/bin/env python3
"""run.py — the execution envelope every scheduled job goes through.

Closes bug classes 2 and 3 together, because they are the same defect seen
from two sides: nothing enforces the boundary between "it ran" and "it worked".

CLASS 2 — interactive environment != scheduled environment.
The usual fix is a test that runs the job under a minimal env. That is a
test for a difference; this removes the difference. The runner builds ONE
normalized environment and uses it whether it was invoked by cron, by
launchd, or by a human at a prompt. "Works by hand" and "works on schedule"
become the same statement.

    shutil.which("nlm") resolved in a login shell and failed under launchd,
    because ~/go/bin is not on the PATH launchd hands a job. The script
    reported "nlm is not installed on this machine" for three days while the
    binary sat in ~/go/bin, and podcast auth aged out on two servers.
    OLLAMA_MODEL unset in cron logged `<unset>` for weeks the same way.

CLASS 3 — failures that do not fail.
A job may not report success without evidence. Every declared artifact must
exist, and must either have ADVANCED during the run or already be fresh. A
step that runs, writes nothing, and exits 0 is a failure here.

    `nlm audio download` prints a URL, exits class 3 and writes no file.
    Nightshift reported "0 completed, 0 failed, 0 skipped" for a 20-task
    queue. `cmd | tail` returns tail's status, not the command's.

EXIT CODES are distinct so a caller can tell these apart, which the old
"non-zero means something" convention could not:

    0  post-conditions satisfied
    1  ran, but an artifact is missing, stale or does not match
    2  the command itself failed
    3  a precondition failed — required env missing; the job never ran
    4  the job is not in the manifest

    run.py <job-name>              # run under the contract
    run.py <job-name> --dry-run    # show the env and contract, run nothing
    run.py --list                  # jobs on this machine
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

import yaml

ROOT = pathlib.Path(os.environ.get("DATACORE_ROOT", pathlib.Path.home() / "Data"))
MANIFEST = ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
HOME = pathlib.Path.home()

# The one environment. Deliberately close to what launchd and cron actually
# provide, plus the paths this fleet's tools genuinely install into. Adding a
# directory here is a considered decision that applies everywhere at once --
# which is the point. Previously each script discovered its own PATH problem
# separately, in production, days later.
BASE_PATH = ":".join([
    str(HOME / "go" / "bin"),        # nlm and other Go tools
    str(HOME / ".pyenv" / "shims"),
    "/opt/homebrew/bin", "/usr/local/bin",
    "/usr/bin", "/bin", "/usr/sbin", "/sbin",
])

# Passed through when present. Everything else is dropped, so a job cannot
# silently depend on a variable that happens to exist in a developer's shell.
PASSTHROUGH = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH",
               "SSH_AUTH_SOCK", "DATACORE_ROOT", "DATACORE_ACTOR", "DATACORE_MACHINE")


def normalized_env(job: dict) -> dict[str, str]:
    """The environment the job gets, identical for every caller."""
    env = {k: os.environ[k] for k in PASSTHROUGH if k in os.environ}
    env.setdefault("HOME", str(HOME))
    env["PATH"] = BASE_PATH
    env["DATACORE_ROOT"] = str(ROOT)
    env["DATACORE_JOB"] = job["name"]

    # Declared requirements come from the config plane, never from the
    # invoking shell -- that is what makes by-hand and on-schedule identical.
    canonical = HOME / ".datacore" / "datacore.env"
    if canonical.exists():
        for line in canonical.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                env.setdefault(k, v.strip().strip("'\""))
    return env


def _artifact_path(raw: str) -> pathlib.Path:
    p = raw.replace("{today}", datetime.date.today().isoformat())
    return HOME / p[2:] if p.startswith("~/") else pathlib.Path(p)


def _check_artifact(spec: dict, before: float | None) -> tuple[bool, str]:
    """Did this artifact end the run in an acceptable state?

    Acceptable means: it exists, AND either it advanced during this run or it
    was already inside its freshness window. A job that legitimately had
    nothing to do leaves a fresh artifact untouched and passes; a job that
    broke leaves a stale artifact untouched and fails. That distinction is the
    whole point -- the old convention could not express it.
    """
    p = _artifact_path(spec["path"])
    if not p.exists():
        return False, f"artifact absent after run: {p}"

    mtime = p.stat().st_mtime
    advanced = before is None or mtime > before
    max_age = spec.get("max_age_hours")
    fresh = max_age is None or (time.time() - mtime) <= max_age * 3600

    if not advanced and not fresh:
        age_h = (time.time() - mtime) / 3600
        return False, (f"artifact neither advanced nor fresh: {p.name} is "
                       f"{age_h:.1f}h old (max {max_age}h) and was not written")

    check = spec.get("check")
    if check == "nonempty" and p.stat().st_size == 0:
        return False, f"artifact is empty: {p.name}"
    if check == "regex":
        try:
            body = p.read_text(errors="replace")
        except OSError as e:
            return False, f"unreadable: {p.name} ({e})"
        if not re.search(spec["arg"], body, re.M):
            return False, f"{p.name} does not match {spec['arg']!r}"
    return True, f"{p.name} ok" + ("" if advanced else " (unchanged but fresh)")


def run(job: dict, dry: bool = False) -> int:
    env = normalized_env(job)

    missing = [v for v in job.get("required_env", []) if not env.get(v)]
    if missing:
        print(f"PRECONDITION FAILED — required env missing: {', '.join(missing)}")
        print("  The job was NOT run. This is exit 3, distinct from a job that "
              "ran and failed.")
        return 3

    artifacts = job.get("artifacts", [])
    before = {}
    for a in artifacts:
        p = _artifact_path(a["path"])
        before[a["path"]] = p.stat().st_mtime if p.exists() else None

    if dry:
        print(f"job      {job['name']}  ({job['machine']}, {job['schedule']})")
        print(f"cmd      {job['cmd']}")
        print(f"PATH     {env['PATH']}")
        print(f"required {job.get('required_env') or '(none)'}")
        for a in artifacts:
            print(f"expects  {a['path']}  check={a.get('check')} "
                  f"max_age={a.get('max_age_hours')}")
        return 0

    # pipefail matters: `cmd | tail` otherwise reports tail's status. That is
    # engram ENG-2026-08-19-018 and it recurs because nothing enforces it.
    started = time.time()
    proc = subprocess.run(["/bin/bash", "-o", "pipefail", "-c", job["cmd"]],
                          env=env, capture_output=True, text=True)
    took = time.time() - started

    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    if proc.returncode != 0:
        print(f"COMMAND FAILED — exit {proc.returncode} after {took:.1f}s")
        return 2

    failures = []
    for a in artifacts:
        ok, detail = _check_artifact(a, before[a["path"]])
        print(f"  {'ok  ' if ok else 'FAIL'} {detail}")
        if not ok:
            failures.append(detail)

    if failures:
        print(f"POST-CONDITION FAILED — the command exited 0 but produced no "
              f"acceptable output ({len(failures)} artifact(s))")
        return 1

    print(f"OK — {job['name']} satisfied its contract in {took:.1f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("job", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    doc = yaml.safe_load(MANIFEST.read_text())
    jobs = {j["name"]: j for j in doc["jobs"]}

    if a.list or not a.job:
        machine = os.environ.get("DATACORE_MACHINE", "mac")
        for n, j in sorted(jobs.items()):
            mark = "*" if j["machine"] == machine else " "
            print(f" {mark} {n:<28} {j['machine']:<11} {j['schedule']}")
        print(f"\n* = this machine ({machine})")
        return 0

    if a.job not in jobs:
        print(f"no job named {a.job!r} in the manifest. A job that is not "
              f"declared cannot be verified — that is bug class 4.")
        return 4
    return run(jobs[a.job], dry=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
