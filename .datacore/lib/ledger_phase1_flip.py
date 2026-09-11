#!/usr/bin/env python3
"""Flip one space to Phase 1, or reverse it -- the drill's steps, on a real space.

FLIP     write `.datacore/ledger-phase` = 1, ignore org/next_actions.org,
         drop it from the index, generate it from the ledger, commit.
REVERSE  remove the marker and the ignore line, commit the current generated
         file as the authored file again.

Preconditions for FLIP, checked, not assumed: the ledger folds; the space's
last ingest left nothing unimported; org and projection agree on every live
item (the shadow diff is clean for this space). Refuses otherwise.

    ledger_phase1_flip.py --space NAME (--flip | --reverse) [--root DIR] [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger_project_org import MARKER, ORG, phase, project_space  # noqa: E402
from ledger_transport import _repo_lock  # noqa: E402
from org_transaction import delete_file, serialized, watch_file, write_org_text  # noqa: E402
from file_utils import fsync_directory  # noqa: E402

IGNORE_LINE = "org/next_actions.org"
PENDING = Path(".datacore/state/phase-transition.json")


def git(space: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(space), *args], capture_output=True, text=True)


def shadow_clean(space: Path) -> tuple[bool, str]:
    from ledger.genesis import scan
    before = scan(space)
    unimported = len(before.importable)
    if unimported:
        return False, f"{unimported} org task(s) not yet in the ledger — ingest first"
    # The ledger also owns independent items. Absence from this source file
    # is not evidence for deleting them. project_space checks full source
    # preservation before publication and may safely add those extra items.
    return True, 'source IDs admitted; full content preservation checked during projection'


class TransitionRefused(RuntimeError):
    pass


@serialized
def _prepare(space: Path, destination: int):
    """Persist a complete local transition, or recover ALL its files.

    Git publication follows this transaction. A failed hook/commit leaves a
    complete local transition with a durable pending record; retry publishes
    it. It must not pretend that the new phase has already been committed.
    """
    for path in (MARKER, ORG, PENDING, Path(".gitignore")):
        if (space / path).is_symlink():
            raise TransitionRefused("transition paths must not be symbolic links")
        watch_file(space / path)
    if destination == 1:
        ok, why = shadow_clean(space)
        if not ok:
            raise TransitionRefused(why)
        write_org_text(space / MARKER, "1\n")
    result = project_space(space)
    if not result.startswith("generated"):
        raise TransitionRefused(result)
    gi = space / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if destination == 1:
        if IGNORE_LINE not in lines:
            lines += ["# Phase 1 (DIP-0046): generated from the ledger", IGNORE_LINE]
    else:
        lines = [line for line in lines if line != IGNORE_LINE and not line.startswith("# Phase 1 (DIP-0046):")]
        delete_file(space / MARKER)
    write_org_text(gi, "\n".join(lines) + ("\n" if lines else ""))
    write_org_text(space / PENDING, json.dumps({"version": 1, "phase": destination}) + "\n")


def _publish(space: Path, destination: int):
    """Use an isolated index; hooks still run, other staged work is untouched.

    Hold Git's real index lock through commit and index installation. Failure
    before commit preserves the original index byte for byte. A crash after
    commit leaves Git's conventional index.lock for explicit recovery.
    """
    result = git(space, "rev-parse", "--path-format=absolute", "--git-path", "index")
    if result.returncode:
        raise TransitionRefused("cannot locate the repository index")
    index = Path(result.stdout.strip())
    lock = index.with_name(index.name + ".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temporary = None
    try:
        if git(space, "diff", "--cached", "--quiet").returncode:
            raise TransitionRefused("index changed before publication; preserving staged work")
        tmp_fd, tmp_name = tempfile.mkstemp(prefix="phase-index-", dir=index.parent)
        os.close(tmp_fd)
        temporary = Path(tmp_name)
        temporary.unlink()  # Git requires an absent index or a valid index.
        env = {**os.environ, "GIT_INDEX_FILE": str(temporary)}
        def checked(*args):
            proc = subprocess.run(["git", "-C", str(space), *args], env=env,
                                  capture_output=True, text=True)
            if proc.returncode:
                # Hook output can contain user data or credentials.
                raise TransitionRefused(f"Git {args[0]} failed (exit {proc.returncode}); local transition remains pending")
            return proc
        checked("read-tree", "HEAD")
        checked("add", "--", ".gitignore")
        if destination == 1:
            checked("add", "--", str(MARKER))
            checked("rm", "--cached", "--ignore-unmatch", "--", str(ORG))
        else:
            checked("add", "--", str(ORG))
            checked("rm", "--cached", "--ignore-unmatch", "--", str(MARKER))
        changed = checked("diff", "--cached", "--name-only").stdout.strip()
        if changed:
            checked("commit", "-q", "-m", f"phase {destination}: transition Org ownership (DIP-0046)")
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(temporary.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(lock, index)
        fsync_directory(index.parent)
    finally:
        if fd is not None:
            os.close(fd)
        lock.unlink(missing_ok=True)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@serialized
def _clear_pending(space):
    delete_file(space / PENDING)


def _transition(space: Path, destination: int, apply: bool) -> int:
    with _repo_lock(space):
        try:
            pending_path = space / PENDING
            pending = json.loads(pending_path.read_text()) if pending_path.exists() else None
            if pending is not None and pending != {"version": 1, "phase": destination}:
                raise TransitionRefused("a different or invalid transition is pending; reconcile it first")
            if phase(space) == destination and pending is None:
                print(f"  already Phase {destination}")
                return 0
            # An isolated index must not turn another caller's staged changes
            # into a misleading inverse diff after advancing HEAD.
            clean = git(space, "diff", "--cached", "--quiet")
            if clean.returncode:
                raise TransitionRefused("index is not clean or cannot be inspected; commit/stash staged work first")
            if not apply:
                print(f"  dry run — would validate content, transition to Phase {destination}, then commit")
                return 0
            if pending is None:
                _prepare(space, destination)
            elif phase(space) != destination:
                raise TransitionRefused("pending transition and phase marker disagree")
            _publish(space, destination)
            _clear_pending(space)
            print(f"  Phase {destination} committed")
            return 0
        except (TransitionRefused, OSError, ValueError) as exc:
            print(f"  REFUSED — {exc}")
            return 1


def flip(space: Path, apply: bool) -> int:
    return _transition(space, 1, apply)


def reverse(space: Path, apply: bool) -> int:
    return _transition(space, 0, apply)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")))
    ap.add_argument("--space", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--flip", action="store_true"); g.add_argument("--reverse", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    space = a.root / a.space
    return flip(space, a.apply) if a.flip else reverse(space, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
