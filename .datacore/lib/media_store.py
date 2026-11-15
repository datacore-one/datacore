#!/usr/bin/env python3
"""Each space's media/ folder: large files outside git (owner, 2026-11-14).

The space commit hook refuses a binary of 10 MB or more (DIP-0015 tier 2).
Such a file lives in <space>/media/ under the path it would have had, and a
relative symlink stays at the old place, so it is still found where it belongs.
The symlink is a few bytes and commits normally; on a machine without the
media file it dangles, which is expected. media/ itself is ignored by git and
copied with rsync to the storage host named by `roles.media_backup` in the
private registry (registry/infrastructure.yaml), never deleting there.

  media_store.py move <file>...        move into media/, leave a symlink
  media_store.py ignore                add /media/ to every space's .gitignore
  media_store.py backup [--dry-run]    rsync every space's media/ to the storage host
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[2]
SPACE_RE = re.compile(r"^\d+-[\w.-]+$")
IGNORE_LINE = "/media/"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]


class MediaError(Exception):
    pass


def space_of(path: Path) -> Path:
    for parent in path.parents:
        if SPACE_RE.match(parent.name) and not parent.name.endswith("-archive") \
                and (parent / ".git").exists():
            return parent
    raise MediaError(f"{path} is not inside a space repository")


def ensure_ignored(space: Path) -> bool:
    """Add /media/ to the space's .gitignore; False when it is already there."""
    gi = space / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    if any(line.strip() in ("/media/", "/media", "media/", "media") for line in text.splitlines()):
        return False
    sep = "" if not text or text.endswith("\n") else "\n"
    gi.write_text(f"{text}{sep}# Large files, outside git; backed up by media_store.py\n{IGNORE_LINE}\n")
    return True


def move(path: Path | str) -> Path:
    path = Path(os.path.abspath(path))
    if path.is_symlink() or not path.is_file():
        raise MediaError(f"{path} is not a regular file")
    space = space_of(path)
    rel = path.relative_to(space)
    if rel.parts[0] == "media":
        raise MediaError(f"{path} is already in media/")
    dest = space / "media" / rel
    if dest.exists() or dest.is_symlink():
        raise MediaError(f"{dest} already exists; nothing moved")
    ensure_ignored(space)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    path.symlink_to(os.path.relpath(dest, path.parent))
    return dest


def default_dest() -> str | None:
    """MEDIA_BACKUP_DEST, else roles.media_backup ("user@host:/path") in the private registry.

    Shipped code names no host (registry/infrastructure.yaml.example).
    """
    if os.environ.get("MEDIA_BACKUP_DEST"):
        return os.environ["MEDIA_BACKUP_DEST"]
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "jobs"))
        from manifest import role
        return role("media_backup")
    except Exception:  # noqa: BLE001
        return None


def _host() -> str:
    try:
        from actor_identity import this_actor
        return this_actor()
    except Exception:  # noqa: BLE001 -- a name for a folder, never a reason to fail
        import socket
        return socket.gethostname().split(".")[0].lower()


def backup(root: Path, dest: str, *, host: str | None = None,
           dry_run: bool = False) -> int:
    """Copy every space's media/ to <dest>/<host>/<space>/. Returns the number of failures."""
    host = host or _host()
    remote, _, base = dest.rpartition(":") if ":" in dest else ("", "", dest)
    failures = copied = 0
    for space in sorted(Path(root).glob("[0-9]-*")):
        media = space / "media"
        if not media.is_dir() or space.name.endswith("-archive"):
            continue
        copied += 1
        target = f"{base}/{host}/{space.name}"
        if remote:
            mk = subprocess.run([*SSH, remote, "mkdir", "-p", target], capture_output=True, text=True)
            ok, why = mk.returncode == 0, (mk.stderr or mk.stdout)
        else:
            try:
                Path(target).mkdir(parents=True, exist_ok=True)
                ok, why = True, ""
            except OSError as e:
                ok, why = False, str(e)
        if ok:
            # Never --delete: a file removed here stays on the storage host.
            cmd = ["rsync", "-a", *(["--dry-run"] if dry_run else []), "-e", " ".join(SSH),
                   f"{media}/", f"{remote}:{target}/" if remote else f"{target}/"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            ok, why = r.returncode == 0, (r.stderr or r.stdout)
        failures += not ok
        print(f"[media] {space.name}: {'ok' if ok else 'FAILED: ' + why.strip()[:200]}")
    # One last line for the scheduled job's verifier (job_verify last_line_regex).
    print(f"media_store: ok {copied} space(s)" if not failures
          else f"media_store: FAILED {failures} of {copied} space(s)")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mv = sub.add_parser("move", help="move files into their space's media/ and leave a symlink")
    mv.add_argument("paths", nargs="+")
    sub.add_parser("ignore", help="add /media/ to every space's .gitignore")
    bk = sub.add_parser("backup", help="rsync every space's media/ to the storage host")
    bk.add_argument("--dest", default=None)
    bk.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "move":
        bad = 0
        for p in a.paths:
            try:
                print(f"[media] {p} -> {move(p)}")
            except MediaError as e:
                print(f"[media] {e}", file=sys.stderr)
                bad += 1
        return 1 if bad else 0
    if a.cmd == "ignore":
        for space in sorted(DATA.glob("[0-9]-*")):
            if (space / ".git").exists() and not space.name.endswith("-archive"):
                print(f"[media] {space.name}: {'added' if ensure_ignored(space) else 'already ignored'}")
        return 0
    dest = a.dest or default_dest()
    if not dest:
        print("[media] no backup destination: set roles.media_backup in "
              "registry/infrastructure.yaml or MEDIA_BACKUP_DEST", file=sys.stderr)
        return 2
    return 1 if backup(DATA, dest, dry_run=a.dry_run) else 0


if __name__ == "__main__":
    sys.exit(main())
