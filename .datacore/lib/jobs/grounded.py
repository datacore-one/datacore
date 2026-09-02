#!/usr/bin/env python3
"""grounded.py — bind jobs/manifest.yaml to the filesystem and the schedulers.

WHY. DIP-0035 built the verifier and explicitly left this out: adding a job
means "add one entry to jobs/manifest.yaml", a convention with nothing behind
it. Two consequences, both live on 2026-09-02:

  box-ledger-verify   checks ~/.datacore/state/ledger-verify.log for staleness.
                      No script anywhere writes that file. The check has never
                      had a producer and never could pass.
  box-registry-gc     names registry_gc.py, which exists on disk and appears in
                      no crontab. The check has a producer that never runs.

Both had been failing daily and were indistinguishable, from the alert, from a
job that ran and found problems. That is bug class 4, and it is what this
closes: a check may not outlive its producer, and a producer may not be
declared without being scheduled.

THREE BINDINGS, checked separately because they fail for different reasons:

  cmd      the script a job claims to run exists. Repo-relative paths are
           checkable anywhere, so this runs in CI with no hosts reachable.
  sched    the declared `schedule` matches what the machine actually runs.
           Needs the host, so it is opt-in (--live) and reports `n-a` when a
           host is unreachable -- never `ok`. An unreachable host is not a
           pass; that rule is inherited from the credential broker.
  orphan   every artifact carrying `max_age_hours` belongs to a job whose cmd
           exists. A freshness check with no producer is the ledger-verify bug.

Exit 1 if any binding is broken, so this can gate a commit.

    grounded.py                 # static bindings only (CI-safe)
    grounded.py --live          # also diff declared schedules against hosts
    grounded.py --machine box   # one machine
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
HOME = pathlib.Path.home()

# Which machine this checker is running on. Only `mac` jobs can have their
# schedule read without ssh.
LOCAL = os.environ.get("DATACORE_MACHINE", "mac")


def _resolve(cmd: str) -> pathlib.Path | None:
    """First filesystem path in a cmd string, resolved.

    `cmd` is a shell fragment: it may carry env assignments, `cd X && ...`,
    redirections and arguments. The thing being bound is the script, so take
    the first token that looks like a path.
    """
    def _abs(tok: str) -> pathlib.Path:
        tok = tok.split(">")[0].strip()
        if tok.startswith("~/"):
            return HOME / tok[2:]
        if tok.startswith("/"):
            return pathlib.Path(tok)
        return ROOT / tok

    # `cd DIR && ./script.sh` -- a relative token after a cd is relative to
    # DIR, not to the repo root. Missing this resolved
    # `cd ~/Data/.datacore/secrets && ./scripts/sync.sh` to
    # ~/Data/scripts/sync.sh, a path that has never existed, and reported the
    # credential-distribution script as unversioned. It is gitignored like its
    # three siblings.
    cd = re.search(r"\bcd\s+(\S+)", cmd)
    if cd:
        base = cd.group(1)
        base_p = (HOME / base[2:]) if base.startswith("~/") else (
            pathlib.Path(base) if base.startswith("/") else ROOT / base)
        for tok in re.split(r"\s+", cmd.strip()):
            if tok.startswith("./") and tok.endswith((".sh", ".py", ".ts", ".mjs")):
                return base_p / tok[2:]

    cands = [t for t in re.split(r"\s+", cmd.strip())
             if t.startswith(("~/", "/", "./")) or "/" in t]
    if not cands:
        return None
    # Prefer a script over a directory. `cd ~/x/lens && python3 -m lib.runner`
    # names a directory first and the thing that runs never as a path -- taking
    # the first token there resolved to a directory, which git does not track,
    # so the vcs check reported "untracked production code" about a folder.
    for t in cands:
        if t.endswith((".sh", ".py", ".ts", ".mjs")):
            return _abs(t)
    return _abs(cands[0])


_TRACKED: set[str] | None = None


def _tracked(path: pathlib.Path) -> bool:
    """Is this path tracked in the repo? Cached; one `git ls-files` per run.

    This replaces a path-prefix heuristic. "Under ~/Data" is not the same as
    "in the repo" -- servers keep untracked files there too -- and conflating
    them made this checker report five working jobs as broken.
    """
    global _TRACKED
    if _TRACKED is None:
        try:
            r = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                               capture_output=True, text=True, timeout=60)
            _TRACKED = {str(ROOT / line) for line in r.stdout.splitlines()}
        except (subprocess.SubprocessError, OSError):
            _TRACKED = set()
    return str(path) in _TRACKED


def _ignored(path: pathlib.Path) -> bool:
    """Is this path deliberately gitignored?

    Untracked and ignored are different facts. `.gitignore:312` reads
    "Machine-local operational scripts -- private, never tracked here", so the
    fourteen cos_*.sh jobs are a policy, not an oversight. Failing them would
    report a decision as a defect -- which is the exact error class this whole
    checker exists to catch, and it very nearly shipped that way.
    """
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(path)],
                           capture_output=True, timeout=20)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _crontab(host: str | None) -> str | None:
    """That machine's crontab, or None if it could not be read.

    None means unknown and is reported as n-a. It is never treated as empty,
    because "no crontab" and "could not reach the host" are different facts
    and conflating them is how a broken thing looks healthy.
    """
    cmd = ["crontab", "-l"] if host is None else [
        "ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, "crontab -l"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except (subprocess.SubprocessError, OSError):
        return None
    return r.stdout if r.returncode == 0 else None


def _systemd(host: str | None) -> str | None:
    cmd = ["systemctl", "list-timers", "--all", "--no-pager"] if host is None else [
        "ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host,
        "systemctl list-timers --all --no-pager 2>/dev/null"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except (subprocess.SubprocessError, OSError):
        return None
    return r.stdout if r.returncode == 0 else None


def check(live: bool = False, machine: str | None = None) -> list[dict]:
    doc = yaml.safe_load(MANIFEST.read_text())
    jobs = [j for j in doc["jobs"] if not machine or j["machine"] == machine]

    sched_cache: dict[str, tuple[str | None, str | None]] = {}
    findings = []

    for j in jobs:
        name, mach, cmd = j["name"], j["machine"], j.get("cmd", "")
        script = _resolve(cmd)

        # --- cmd binding -------------------------------------------------
        if not cmd:
            findings.append({"job": name, "binding": "cmd", "state": "FAIL",
                             "detail": "no cmd declared"})
        elif script is None:
            findings.append({"job": name, "binding": "cmd", "state": "n-a",
                             "detail": f"no path in cmd: {cmd[:60]}"})
        elif script.exists():
            findings.append({"job": name, "binding": "cmd", "state": "ok",
                             "detail": str(script)})
        elif _tracked(script):
            # Tracked in git and absent here: the repo says it should exist.
            findings.append({"job": name, "binding": "cmd", "state": "FAIL",
                             "detail": f"tracked in git but absent: {script}"})
        elif mach == LOCAL:
            findings.append({"job": name, "binding": "cmd", "state": "FAIL",
                             "detail": f"absent on this machine: {script}"})
        else:
            # Untracked AND on another machine. It may exist there perfectly
            # well -- five box scripts do. Absence here proves nothing, so this
            # is n-a until --live can stat it on the host. Reading it as FAIL
            # was the first version's bug: it assumed everything under ~/Data
            # is repo content, and ~/Data on a server is not.
            findings.append({
                "job": name, "binding": "cmd", "state": "n-a",
                "detail": f"untracked, on {mach} — cannot verify from here "
                          f"(use --live): {script}"})

        # --- orphan binding ----------------------------------------------
        aged = [a for a in j.get("artifacts", []) if a.get("max_age_hours")]
        if aged and script is not None and not script.exists():
            if _tracked(script) or mach == LOCAL:
                findings.append({
                    "job": name, "binding": "orphan", "state": "FAIL",
                    "detail": f"{len(aged)} freshness check(s) with no producer "
                              f"— they can never pass"})

        # --- vcs binding: is production code under version control? ---------
        # Independent of whether the script runs. Five box scripts run daily
        # from ~/Data/.datacore/lib/ on winston and are in no repository: if
        # that host is lost, so are they. Found 2026-09-02 while fixing the
        # cmd check above, which is the only reason it surfaced at all.
        if (script is not None and str(script).startswith(str(HOME / "Data"))
                and not script.is_dir()
                and script.suffix in (".sh", ".py", ".ts", ".mjs")):
            if _tracked(script):
                pass
            elif _ignored(script):
                # Deliberate. Recorded, not failed -- but it is still true that
                # losing the host loses the script, so it is worth seeing.
                findings.append({
                    "job": name, "binding": "vcs", "state": "policy",
                    "detail": f"gitignored by policy — lives only on {mach}: "
                              f"{script.name}"})
            else:
                findings.append({
                    "job": name, "binding": "vcs", "state": "FAIL",
                    "detail": f"untracked and NOT gitignored — unversioned "
                              f"production code: {script.name}"})

        # --- schedule binding --------------------------------------------
        if not live:
            continue
        if script is not None and not script.exists() and mach != LOCAL:
            r = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", mach,
                 f"test -e {script} && echo YES || echo NO"],
                capture_output=True, text=True, timeout=45)
            ok = r.returncode == 0 and "YES" in r.stdout
            findings.append({
                "job": name, "binding": "cmd@host",
                "state": "ok" if ok else ("FAIL" if r.returncode == 0 else "n-a"),
                "detail": (f"exists on {mach}" if ok else
                           f"absent on {mach}: {script}" if r.returncode == 0
                           else f"{mach} unreachable — not a pass")})

        if mach not in sched_cache:
            host = None if mach == LOCAL else mach
            sched_cache[mach] = (_crontab(host), _systemd(host))
        cron, timers = sched_cache[mach]
        if cron is None and timers is None:
            findings.append({"job": name, "binding": "sched", "state": "n-a",
                             "detail": f"{mach} unreachable — not a pass"})
            continue
        stem = script.name if script else ""
        seen = (stem and ((cron or "") + (timers or "")).find(stem) >= 0)
        findings.append({
            "job": name, "binding": "sched",
            "state": "ok" if seen else "FAIL",
            "detail": (f"{stem} found on {mach}" if seen
                       else f"declared '{j.get('schedule')}' but {stem or cmd[:40]} "
                            f"appears in no crontab or timer on {mach}")})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true",
                    help="also diff declared schedules against the hosts")
    ap.add_argument("--machine")
    ap.add_argument("--quiet", action="store_true", help="only FAIL rows")
    a = ap.parse_args()

    rows = check(live=a.live, machine=a.machine)
    fails = [r for r in rows if r["state"] == "FAIL"]
    nas = [r for r in rows if r["state"] == "n-a"]

    for r in rows:
        if a.quiet and r["state"] != "FAIL":
            continue
        mark = {"ok": "  ok ", "FAIL": "FAIL ", "n-a": " n-a ",
                "policy": " pol "}[r["state"]]
        print(f"{mark} {r['job']:<28} {r['binding']:<7} {r['detail'][:74]}")

    print("-" * 96)
    pol = [r for r in rows if r["state"] == "policy"]
    print(f"{len(rows)} binding(s) · {len(fails)} FAIL · {len(nas)} n-a · "
          f"{len(pol)} by policy   (n-a means could not tell, never a pass)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
