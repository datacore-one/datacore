#!/usr/bin/env python3
"""Keep git repositories packed without losing anything.

Git stores each new version of a file as a separate compressed ("loose") object and
only later packs similar versions together as differences. Its automatic packing
triggers on the NUMBER of loose objects, and plumbing commits (hash-object,
commit-tree, as the memory sync makes) never trigger it at all. A repository that
re-commits a few large files therefore grows without bound: the Mac's memory store
reached 5.3 GB of loose objects that pack to 135 MB (2026-11-14).

Everything here is lossless: no commit, reflog entry or unreachable object is removed
(prune and reflog expiry are switched off). History is never rewritten.

  git_compact.py ROOT...             full compaction of every repository found
  git_compact.py --if-heavy REPO...  pack only when loose objects pass the size limit
  git_compact.py --dry-run ROOT...   list repositories and their sizes

Limits (environment): GIT_COMPACT_LOOSE_MB (default 100), GIT_COMPACT_MAX_PACKS (default 20).
Stdlib only, single file: it is also piped to hosts and pods as `python3 - ...`.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SKIP = {"node_modules", ".venv", "venv", "target", ".next", "dist", "__pycache__", ".lake", ".git",
        ".cache", ".pnpm-store", "site-packages"}
LOSSLESS = ["-c", "gc.reflogExpire=never", "-c", "gc.reflogExpireUnreachable=never",
            "-c", "gc.pruneExpire=never", "-c", "pack.threads=2", "-c", "pack.windowMemory=300m"]
LOCK_STALE_S = 3 * 3600
# Git's own packing after porcelain commits and fetches starts at 6,700 loose objects or
# 50 packs; a repository that commits a few large files reaches neither. Compaction lowers both.
AUTO_LOOSE = 500
AUTO_PACKS = 20


def _git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} in {repo}: {r.stderr.strip()[:300]}")
    return r.stdout


def stats(repo) -> dict:
    """Loose and packed size in KB, pack count, from `git count-objects -v`."""
    out = dict(l.split(": ", 1) for l in _git(repo, "count-objects", "-v").splitlines() if ": " in l)
    return {"loose_kb": int(out.get("size", 0)), "loose": int(out.get("count", 0)),
            "packs": int(out.get("packs", 0)), "pack_kb": int(out.get("size-pack", 0)),
            "garbage_kb": int(out.get("size-garbage", 0))}


class _Lock:
    """One compaction per repository at a time; a lock older than LOCK_STALE_S is taken over."""

    def __init__(self, repo):
        gd = _git(repo, "rev-parse", "--absolute-git-dir").strip()
        self.path = Path(gd) / "git-compact.lock"
        self.held = False

    def __enter__(self):
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                self.path.unlink()
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            pass
        return self

    def __exit__(self, *exc):
        if self.held:
            self.path.unlink(missing_ok=True)


def compact(repo) -> dict:
    """Full lossless compaction (`git gc` with prune and reflog expiry off)."""
    before = stats(repo)
    with _Lock(repo) as lock:
        if not lock.held:
            return {"result": "busy", "before": before, "after": before}
        _git(repo, *LOSSLESS, "gc", "--quiet", "--prune=never")
        _git(repo, "config", "gc.auto", str(AUTO_LOOSE))
        _git(repo, "config", "gc.autoPackLimit", str(AUTO_PACKS))
    return {"result": "compacted", "before": before, "after": stats(repo)}


def pack_if_heavy(repo, loose_mb: float | None = None, max_packs: int | None = None) -> str:
    """Pack loose objects once they pass `loose_mb`; consolidate packs past `max_packs`.

    Returns "skipped", "repacked", "compacted" or "busy". `git repack -d` packs only
    reachable loose objects and removes only loose copies now in a pack, so nothing is lost.
    """
    loose_mb = float(os.environ.get("GIT_COMPACT_LOOSE_MB", 100)) if loose_mb is None else loose_mb
    max_packs = int(os.environ.get("GIT_COMPACT_MAX_PACKS", 20)) if max_packs is None else max_packs
    s = stats(repo)
    if s["packs"] > max_packs:
        return compact(repo)["result"]
    if s["loose"] == 0 or s["loose_kb"] <= loose_mb * 1024:
        return "skipped"
    with _Lock(repo) as lock:
        if not lock.held:
            return "busy"
        _git(repo, "-c", "pack.threads=2", "-c", "pack.windowMemory=300m", "repack", "-d", "-q")
    if stats(repo)["loose"] > 0:  # unreachable objects stay loose by design; pack them too, still lossless
        return compact(repo)["result"]
    return "repacked"


def find_repos(roots) -> list[str]:
    found = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            if os.path.exists(os.path.join(dp, ".git")):
                found.append(dp)
            dn[:] = [d for d in dn if d not in SKIP]
    return found


def _du_kb(path) -> int:
    o = subprocess.run(["du", "-sk", path], capture_output=True, text=True).stdout
    return int(o.split()[0]) if o else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--if-heavy", action="store_true", help="pack only repositories past the size limit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.if_heavy:
        rc = 0
        for p in a.paths:
            try:
                res = pack_if_heavy(p)
            except Exception as e:  # a hook caller must never fail
                res, rc = f"error: {e}", 1
            if not a.quiet:
                print(f"{res}  {p}")
        return rc
    repos = find_repos(a.paths)
    tot_b = tot_a = 0
    errors = 0
    for r in repos:
        gd = os.path.join(r, ".git")
        b = _du_kb(gd)
        tot_b += b
        if a.dry_run:
            tot_a += b
            print(f"{b/1024:9.0f} MB  {r}")
            continue
        commits = _git(r, "rev-list", "--all", "--count", check=False).strip()
        try:
            res = compact(r)["result"]
        except Exception as e:
            res, errors = f"error: {str(e)[:120]}", errors + 1
        after_commits = _git(r, "rev-list", "--all", "--count", check=False).strip()
        if after_commits != commits:
            res, errors = f"COMMIT COUNT CHANGED {commits} -> {after_commits}", errors + 1
        aft = _du_kb(gd)
        tot_a += aft
        if not a.quiet or res != "compacted":
            print(f"{b/1024:9.0f} -> {aft/1024:7.0f} MB  commits {commits}  {res}  {r}", flush=True)
    print(f"TOTAL {len(repos)} repositories: {tot_b/1048576:.2f} GB -> {tot_a/1048576:.2f} GB, errors {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
