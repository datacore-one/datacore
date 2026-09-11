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
import glob
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
for library in (pathlib.Path(__file__).resolve().parents[1], ROOT / ".datacore" / "lib"):
    if (library / "process_run.py").is_file():
        sys.path.insert(0, str(library))
        break
from process_run import run as run_process
MANIFEST = ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
# Set from --manifest in main(): the runner deployment keeps its manifest under
# ~/.datacore/v2-runner while jobs' data root (ROOT) stays ~/Data.
MANIFEST_OVERRIDE: pathlib.Path | None = None
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
    #
    # ONLY the variables the job declares (`env:` plus `required_env:`) are
    # taken from the canonical file. The first version copied the whole file
    # into every job, so any job's stray env dump would have exposed every
    # credential on the host, not its own. Independent review 2026-09-03.
    allowed = set(job.get("env") or []) | set(job.get("required_env") or [])
    canonical = HOME / ".datacore" / "datacore.env"
    if allowed and canonical.exists():
        for line in canonical.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            if k in allowed and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                value = v.strip()
                if value.startswith(("\"", "'")):
                    parts = shlex.split(value, comments=False)
                    if len(parts) != 1:
                        raise ValueError("invalid quoted job environment value")
                    value = parts[0]
                env.setdefault(k, value)
    return env


def _artifact_path(raw: str) -> pathlib.Path:
    """The file this artifact refers to, expanding a glob to its NEWEST match.

    A glob was never expanded: the path came back containing a literal `*`,
    which no file is ever named, so every job declaring one reported "artifact
    absent after run" forever. Three did (mac-agent-stream-rsync,
    mac-artifact-pull, box-cos-sync) and none of them could ever have passed --
    a check that cannot succeed teaches an operator to ignore it.

    Newest match, because these are dated series (events-<date>.jsonl,
    briefings/<date>/...): the freshness window is the point of the check.
    An unmatched glob still returns the literal path, so the failure message
    stays the honest "absent" rather than a confusing one about a directory.
    """
    p = raw.replace("{today}", datetime.date.today().isoformat())
    path = HOME / p[2:] if p.startswith("~/") else pathlib.Path(p)
    if not any(ch in str(path) for ch in "*?["):
        return path
    matches = sorted(glob.glob(str(path)), key=lambda m: os.stat(m).st_mtime, reverse=True)
    return pathlib.Path(matches[0]) if matches else path


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
    # A CONTINUOUS SERVICE IS VERIFIED BY ITS ARTIFACT, NOT BY RE-RUNNING IT.
    # For a daemon, `cmd` documents what the unit starts; running it here
    # either blocks until something kills it (mac-lens-sync: exit 124, a
    # timeout reported as a failed job) or starts a SECOND copy of a service
    # already running. Both were live on 2026-09-08. "Is it still producing?"
    # is the real question, and the artifact checks below are what answer it.
    continuous = str(job.get("schedule", "")).strip().lower().startswith("continuous")
    took = 0.0
    if continuous:
        print("continuous service — not re-run; its artifact is the verification")
        proc = None
    else:
        started = time.time()
        timeout = job.get("timeout_seconds", 3600)
        if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
            print("PRECONDITION FAILED — timeout_seconds must be in (0, 86400]")
            return 3
        try:
            proc = run_process(["/bin/bash", "-o", "pipefail", "-c", job["cmd"]],
                               env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"COMMAND FAILED — timed out after {timeout}s; foreground process group stopped")
            return 2
        took = time.time() - started

    if proc is not None and proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc is not None and proc.stderr:
        sys.stderr.write(proc.stderr)

    # Detectors exit 1 for "ran, found problems" and 2 for "could not run";
    # only a crash is a command failure here. A job declares the exit codes
    # that mean it RAN (`exit_ok`, default [0]); anything else is exit 2.
    # Found by the mac-seq-gap pilot: its honest "1 unpublished" read as a
    # command failure.
    ok_codes = set(job.get("exit_ok") or [0])
    if proc is not None and proc.returncode not in ok_codes:
        print(f"COMMAND FAILED — exit {proc.returncode} after {took:.1f}s "
              f"(exit_ok={sorted(ok_codes)})")
        return 2
    if proc is not None and proc.returncode != 0:
        print(f"ran with findings — exit {proc.returncode} after {took:.1f}s; the artifact check decides")

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
    ap.add_argument("--manifest", help="manifest to read (default: <DATACORE_ROOT>/.datacore/lib/jobs/manifest.yaml). "
                    "The runner deployment keeps its manifest under ~/.datacore/v2-runner while jobs' data "
                    "root stays ~/Data; conflating the two sent the pilot's job to a root with no spaces.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    global MANIFEST_OVERRIDE
    MANIFEST_OVERRIDE = pathlib.Path(a.manifest).expanduser() if getattr(a, "manifest", None) else None

    manifest_path = MANIFEST_OVERRIDE or MANIFEST
    from jobs.manifest import validate_manifest
    try:
        doc = yaml.safe_load(manifest_path.read_text())
        validate_manifest(doc, roster_path=ROOT / ".datacore/registry/infrastructure.yaml")
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"PRECONDITION FAILED — invalid job manifest: {error}")
        return 3
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
