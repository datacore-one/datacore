#!/usr/bin/env python3
"""Reconcile a stranded publication record, on proof and nothing else.

`publication_state.reserve()` writes `datacore-publication-pending.json` into a
repository's git directory before it publishes, and clears it once the result is
verified. If the branch moves underneath the reservation, it can neither verify
nor clear, so the record stays -- and `require_clear` then refuses EVERY later
publication into that repository: "Unverified publication requires
reconciliation; pending record retained in Git storage". Nothing implemented that
reconciliation.

Measured 2026-09-17 on nightshift: one record from 2026-09-16 22:25:09 blocked all
publication into 0-personal, and the nightly research notes were "retained
locally" three nights running. Its intended content had landed seconds after the
reservation, committed by the transport's autosave (42dec00d3) -- the race that
created the record in the first place, now closed in `reserve()`.

PROOF, per reserved path: the version the reservation intended (read from the
tree it captured, still held under refs/datacore/publication-captures/<token>)
is byte-identical to that path in some commit on the target branch after the
reservation's base. Only when EVERY path is proven is the record cleared; the
capture ref is kept, and the record is copied beside it as publication-intent,
exactly as publication_state does for an unverified clear. If any path cannot be
proven, nothing changes and the unproven paths are named: that is real work
retained locally, and it belongs to a human.

A RECORD THAT CAPTURED NOTHING (expected_tree null: its process stopped
between reserving and capturing) has no intended version to compare. Its proof
is that the CURRENT version of every reserved path is already on origin
(`prove_uncaptured`); the same all-or-nothing rule applies, and a capture ref,
if one exists, is kept like any other.

  publication_reconcile.py [--root ~/Data]            # list records, dry run
  publication_reconcile.py --repo <path> --apply      # clear on proof
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))


def git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout.strip()


def record_path(repo: Path) -> Path:
    _, common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "datacore-publication-pending.json"


def prove_uncaptured(repo: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    """A record that captured nothing: is each path's CURRENT version on origin?

    2026-10-01 03:04 UTC, 3-fds on the nightshift host: a claim reserved its
    publication and its process was replaced before it captured anything. Such
    a record holds no work of its own -- the reserved paths' only copy is the
    working file -- so the proof is about that file: its version (through the
    path's clean filters) is the blob at that path on some origin ref. Every
    path must be proven; a missing file, or no origin ref to compare with,
    refuses. Origin refs are read as last fetched; nothing is fetched here.
    """
    _, listed = git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/")
    tips = [ref for ref in listed.split() if ref != "refs/remotes/origin/HEAD"]
    proven, unproven = [], []
    for path in paths:
        source = repo / path
        if not tips or source.is_symlink() or not source.is_file():
            unproven.append(path)
            continue
        rc, want = git(repo, "hash-object", f"--path={path}", "--", str(source))
        landed = rc == 0 and any(git(repo, "rev-parse", "-q", "--verify", f"{tip}:{path}")[1] == want
                                 for tip in tips)
        (proven if landed else unproven).append(path)
    return proven, unproven


def prove(repo: Path, record: dict) -> tuple[list[str], list[str]]:
    """(proven, unproven) reserved paths."""
    tree = record.get("expected_tree")
    base = record.get("target_head")
    branch = (record.get("target_branch") or "").removeprefix("refs/heads/")
    paths = list(record.get("paths") or [])
    if not tree and paths:
        return prove_uncaptured(repo, paths)
    if not tree or not base or not branch:
        return [], paths or ["(record has no captured tree -- nothing to compare)"]
    tips = [ref for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}")
            if git(repo, "rev-parse", "--verify", "-q", ref)[0] == 0]
    proven, unproven = [], []
    for path in paths:
        rc, want = git(repo, "rev-parse", f"{tree}:{path}")
        if rc != 0:
            # Not in the intended tree: the publication meant to REMOVE it.
            gone = all(git(repo, "cat-file", "-e", f"{tip}:{path}")[0] != 0 for tip in tips)
            (proven if gone and tips else unproven).append(path)
            continue
        landed = False
        for tip in tips:
            _, commits = git(repo, "log", "--format=%H", f"{base}..{tip}", "--", path)
            if any(git(repo, "rev-parse", f"{c}:{path}")[1] == want for c in commits.split()):
                landed = True
                break
        # Unchanged by the publication: the base already held this exact version.
        if not landed and git(repo, "rev-parse", f"{base}:{path}")[1] == want:
            landed = True
        (proven if landed else unproven).append(path)
    return proven, unproven


def reconcile(repo: Path, apply: bool) -> int:
    rp = record_path(repo)
    if not rp.exists():
        print(f"{repo.name}: no pending publication record")
        return 0
    record = json.loads(rp.read_text(encoding="utf-8"))
    proven, unproven = prove(repo, record)
    if not record.get("expected_tree"):
        print(f"{repo.name}: the record captured nothing; proving each reserved path's "
              "current version is on origin")
    print(f"{repo.name}: pending record for {len(record.get('paths') or [])} path(s), "
          f"target {record.get('target_branch')} base {str(record.get('target_head'))[:10]}")
    for p in proven:
        print(f"  landed     {p}")
    for p in unproven:
        print(f"  NOT PROVEN {p}")
    if unproven:
        print("  REFUSED -- unproven paths are real retained work; inspect them before clearing")
        return 1
    if not apply:
        print("  every reserved path landed -- dry run, pass --apply to clear the record")
        return 0
    from worktree_lifecycle import allocate_publication_workspace
    from file_utils import atomic_write_json, fsync_directory
    retained = allocate_publication_workspace(repo) / "publication-intent.json"
    atomic_write_json(retained, record)
    rp.unlink()
    fsync_directory(rp.parent)
    print(f"  cleared; intent kept at {retained}, capture ref "
          f"{record.get('expected_ref', '(none)')} kept")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path.home() / "Data")
    ap.add_argument("--repo", type=Path, help="reconcile one repository")
    ap.add_argument("--apply", action="store_true", help="clear on proof (default: dry run)")
    args = ap.parse_args(argv)
    repos = [args.repo] if args.repo else sorted(
        p for p in [args.root, *args.root.glob("[0-9]-*")] if (p / ".git").exists())
    rc = 0
    for repo in repos:
        repo = repo.resolve()
        if args.repo or record_path(repo).exists():
            rc |= reconcile(repo, args.apply)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
