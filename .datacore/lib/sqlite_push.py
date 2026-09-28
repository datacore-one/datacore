#!/usr/bin/env python3
"""Push a consistent snapshot of one SQLite database to a roster machine.

    sqlite_push.py SRC MACHINE REMOTE_PATH

SRC          the live database on this machine (read; a hot journal left by a
             dead writer is rolled back, as any opener would)
MACHINE      a roster machine name (jobs.manifest.ssh_alias resolves the route)
REMOTE_PATH  where it lands, relative to the remote user's home

WHY. Three cron jobs rsynced live databases straight off disk (2026-09-28): a
copy of a WAL database taken mid-write is not a database, it is a main file and
a -wal that disagree. They also went to a raw IP as root, into /root paths that
no longer existed after the box moved to its own user, so nothing had landed
for weeks and nothing said so. One of them shipped a local auth token as well.

WHAT IT DOES. The sqlite3 backup API copies the database as of one read
transaction, including writes still sitting in the -wal. The copy is switched to
journal_mode=DELETE so it is a single self-standing file: a reader opening it
read-only never needs a -wal beside it. rsync writes to a temp name and renames,
so a reader on the far side sees the previous whole copy or the new whole copy.
The remote size is then read back and compared, and one line is printed:

    sqlite_push: ok <src> -> <alias>:<path> bytes=N remote_bytes=N
    sqlite_push: FAILED <src>: <reason>

Wrap it in atomic_out.sh so that line becomes the job's artifact; the job
contract is a regex on `^sqlite_push: ok `. Exit 0 only on ok.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]


def _alias(machine: str) -> str | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from jobs.manifest import ssh_alias
    return ssh_alias(machine)


def _run(argv: list[str], **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def snapshot(src: Path, dest: Path) -> int:
    """Copy `src` as of one read transaction into `dest`, standalone. Returns bytes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # mode=rw, not ro: a writer killed mid-transaction leaves a hot -journal,
    # and only a writable opener may roll it back (the recovery every normal
    # opener performs). Read-only fails "attempt to write a readonly database".
    # rw never creates a missing file, so an absent source is an error.
    source = sqlite3.connect(f"file:{src}?mode=rw", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
        finally:
            target.close()
    finally:
        source.close()
    return dest.stat().st_size


def _remote_path(raw: str) -> str | None:
    """A file path under the remote home, or None. `~/x` is accepted as `x`."""
    if raw.startswith("~/"):
        raw = raw[2:]
    p = PurePosixPath(raw)
    if not raw or raw.endswith("/") or p.is_absolute() or ".." in p.parts:
        return None
    return str(p)


def push(src: Path, machine: str, remote: str) -> str:
    """Snapshot, send, verify. Returns the ok line; raises RuntimeError on failure."""
    dest = _remote_path(remote)
    if dest is None:
        raise RuntimeError(f"remote path must be a file under the remote home, got {remote!r}")
    if not src.is_file():
        raise RuntimeError("source database does not exist")
    alias = _alias(machine)
    if not alias:
        raise RuntimeError(f"no ssh route to machine {machine!r} in the roster")
    base = Path(os.environ.get("SQLITE_PUSH_TMP") or Path.home() / ".datacore" / "state" / "sqlite-push")
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="snap-", dir=base))
    try:
        snap = work / PurePosixPath(dest).name
        size = snapshot(src, snap)
        parent = str(PurePosixPath(dest).parent)
        steps = [
            ["ssh", *SSH_OPTS, alias, f"mkdir -p -- {shlex.quote(parent)}"],
            ["rsync", "-az", "--timeout=900", "-e", "ssh " + " ".join(SSH_OPTS), str(snap), f"{alias}:{dest}"],
        ]
        for argv in steps:
            p = _run(argv, timeout=7200)
            if p.returncode != 0:
                raise RuntimeError(f"{argv[0]} exited {p.returncode}: {(p.stderr or '').strip()[-200:]}")
        p = _run(["ssh", *SSH_OPTS, alias, f"stat -c %s -- {shlex.quote(dest)}"], timeout=60)
        got = (p.stdout or "").strip()
        if p.returncode != 0 or not got.isdigit():
            raise RuntimeError(f"could not read the remote size (exit {p.returncode})")
        if int(got) != size:
            raise RuntimeError(f"remote size {got} != snapshot size {size}")
        return f"sqlite_push: ok {src} -> {alias}:{dest} bytes={size} remote_bytes={got}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    src = Path(os.path.expanduser(args[0]))
    try:
        print(push(src, args[1], args[2]))
        return 0
    except (RuntimeError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"sqlite_push: FAILED {src}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
