"""Publish machine-written ledger files from every space repo -- and nothing else.

This Mac published its ledger only when /today ran or someone converged by
hand, so `mac-seq-gap` reported unpublished events every hour by construction
(2026-09-03: 102 events in 1-datafund). winston runs its full sync every 15
minutes; that sync autosaves everything, including a human's half-edited
files, which is right for a server and wrong for the workstation someone is
typing on.

So: only the machine-written ledger paths are staged, committed and pushed.
A human's dirty files are never touched -- they stay exactly as dirty as they
were. (Until 2026-09-04 a space with ANY human file dirty was skipped whole,
which meant the workstation's ledger stayed unpublished for as long as the
human had work in progress -- i.e. always, on the two spaces that matter.)

    ledger_publish_safe.py            # do it
    ledger_publish_safe.py --dry-run  # say what would happen
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("DATACORE_ROOT", pathlib.Path.home() / "Data"))

MACHINE_WRITTEN = (
    ".datacore/events/",
    ".datacore/state/venture/cadence-log/",
    ".datacore/checkpoints/",
    ".datacore/state/seq-hwm/",
)


def _git(space: pathlib.Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--literal-pathspecs", "-C", str(space), *args], capture_output=True, text=True, timeout=timeout)


def dirty_tracked(space: pathlib.Path) -> list[str]:
    r = _git(space, 'status', '--porcelain', '-z', '--untracked-files=all', timeout=60)
    if r.returncode:
        raise RuntimeError('cannot establish repository status')
    paths = []
    for entry in r.stdout.split('\0'):
        if not entry:
            continue
        status, name = entry[:2], entry[3:]
        if any(flag in status for flag in 'RCU') or (name.startswith(MACHINE_WRITTEN) and 'D' in status):
            raise RuntimeError('rename, conflict or machine-file deletion requires review')
        paths.append(name)
    return paths


def _outgoing_is_machine_only(space):
    commits = _git(space, 'rev-list', '@{u}..HEAD')
    if commits.returncode:
        return False
    rows = commits.stdout.splitlines()
    if len(rows) > 1000:
        return False  # large historical publication needs explicit review
    for commit in rows:
        paths = _git(space, 'diff-tree', '--root', '-m', '--no-commit-id', '--name-only', '-r', '-z', commit)
        if paths.returncode:
            return False
        for name in paths.stdout.split('\0'):
            if not name or name.startswith(MACHINE_WRITTEN):
                continue
            # A merge may include human files already present upstream. Do
            # not mistake those for new exposure, but inspect every outgoing
            # commit so an added-then-reverted private file cannot ride along.
            local = _git(space, 'ls-tree', '-z', commit, '--', name)
            remote = _git(space, 'ls-tree', '-z', '@{u}', '--', name)
            if local.returncode or remote.returncode or local.stdout != remote.stdout:
                return False
    return True


def only_machine_written(paths: list[str]) -> bool:
    return bool(paths) and all(p.startswith(MACHINE_WRITTEN) for p in paths)


def split(paths: list[str]) -> tuple[list[str], list[str]]:
    """(machine-written, human) -- the first is published, the second is never touched."""
    machine = [p for p in paths if p.startswith(MACHINE_WRITTEN)]
    return machine, [p for p in paths if p not in machine]


def publish(space: pathlib.Path, machine: list[str]) -> tuple[str, str]:
    """Commit exactly `machine`, bring upstream in if behind, push.

    Returns (status, detail): "ok", or "held" (committed locally, could not
    reach or reconcile with origin -- the next run pushes), or "FAIL".
    """
    if machine and not only_machine_written(machine):
        return 'FAIL', 'publication contains a non-machine path'
    for name in machine:
        path = space / name
        if (pathlib.Path(name).is_absolute() or '..' in pathlib.Path(name).parts or path.is_symlink()
                or not path.resolve().is_relative_to(space.resolve())
                or not path.resolve().relative_to(space.resolve()).as_posix().startswith(MACHINE_WRITTEN)):
            return 'FAIL', 'publication path escapes its repository'
    if machine:
        r = _git(space, "add", "--", *machine)
        if r.returncode:
            return "FAIL", f"add: {r.stderr.strip()[-160:]}"
        r = _git(space, "commit", "-q", "-m", f"ledger: publish {len(machine)} machine-written file(s)", "--", *machine)
        if r.returncode:
            return "FAIL", f"commit: {(r.stderr or r.stdout).strip()[-160:]}"
    r = _git(space, "fetch", "-q", timeout=300)
    if r.returncode:
        return "held", f"fetch: {r.stderr.strip()[-120:]}"
    up = _git(space, "rev-parse", "--abbrev-ref", "@{u}")
    if up.returncode:
        return "held", "no upstream branch"
    behind_result = _git(space, "rev-list", "--count", "HEAD..@{u}")
    behind = behind_result.stdout.strip()
    if behind_result.returncode or not behind.isdecimal():
        return 'held', 'cannot establish upstream position'
    if not _outgoing_is_machine_only(space):
        return 'held', 'outgoing history includes unreviewed human changes or cannot be verified'
    if behind != '0':
        m = _git(space, "merge", "--no-edit", "@{u}")
        if m.returncode:
            return "held", 'merge with upstream failed; conflict files and index stages retained'
    if not _outgoing_is_machine_only(space):
        return 'held', 'merged outgoing history requires review'
    r = _git(space, "push", "-q", timeout=300)
    if r.returncode:
        return "held", f"push: {r.stderr.strip()[-160:]}"
    return "ok", ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rc = 0
    # One line per run, always -- a silent run and a run that never happened
    # look identical in the log, and on 2026-09-04 that hid four hourly runs.
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    published = 0
    for space in sorted(p for p in ROOT.glob("[0-9]-*") if (p / ".git").exists()):
        try:
            machine, human = split(dirty_tracked(space))
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            print(f'  FAIL  {space.name}: repository status requires review')
            rc = 1
            continue
        if not machine:
            ahead = _git(space, 'rev-list', '--count', '@{u}..HEAD')
            if ahead.returncode:
                print(f'  held  {space.name}: cannot establish pending publication')
                rc = 1
                continue
            if ahead.stdout.strip() == '0':
                continue
        note = f" (leaving {len(human)} human file(s) untouched)" if human else ""
        if a.dry_run:
            print(f"  would {space.name}: publish {len(machine)} ledger file(s){note}")
            continue
        status, detail = publish(space, machine)
        published += 1
        print(f"  {status:5} {space.name}: {len(machine)} ledger file(s){note}" + (f" -- {detail}" if detail else ""))
        rc = rc or (0 if status == "ok" else 1)
    print(f"{stamp} run complete: {published} space(s) published, rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
