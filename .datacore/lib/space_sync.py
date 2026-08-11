#!/usr/bin/env python3
"""Sync one space repo. A thin shim over `ledger_transport.converge`.

This was 142 lines reimplementing, for the Mac, the algorithm `cos_sync.sh`
already implemented for the box — autosave-commit, fetch, rebase, and on
conflict save to a rescue branch, hard-reset and alert. Two machines, one
algorithm, two copies, and they drifted: the `git push … || true` fix lived on
the box for weeks while this file and the repo's copies still swallowed the
failure (DIP-0046 motivation).

`ledger_transport` is now the single writer, so this keeps only its published
interface — `sync_repo(repo) -> str` — and delegates. Callers
(`gitea_pull_webhook`, `morning_journal`) are unchanged.

Two behaviours deliberately do NOT survive the move, and both were the point:

  NO REBASE. Per-writer logs are disjoint files, so a merge is a union and
  cannot conflict; rebase bought nothing and is the operation that stranded 610
  commits on a parked branch and 645 across 74 run branches.

  NO HARD RESET, NO RESCUE BRANCH. `converge` autosave-COMMITS before merging —
  a commit is on a branch, findable and pushable, where a stash is invisible
  (ENG-2026-0729-009 cost 10 orphaned stashes over six weeks) — and then refuses
  a genuine content conflict rather than resetting past it. Nothing is discarded
  to make the sync succeed, so there is nothing to rescue.

Outcome strings are preserved because `gitea_pull_webhook` branches on them:
'clean' | 'offline' | 'conflict' | 'skipped'. 'rescued' can no longer occur and
is retired.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger_transport import converge  # noqa: E402


def sync_repo(repo: Path, quiet: bool = False) -> str:
    """Sync one repo. Returns 'clean' | 'offline' | 'conflict' | 'skipped'."""
    res = converge(Path(repo))
    if res.ok:
        outcome = "clean"
    elif "not in registry" in res.reason:
        # Refused, not failed: an unregistered repo has no category, so no rule
        # to apply. Silently defaulting is what DIP-0046 §1 forbids.
        outcome = "skipped"
    elif "offline" in res.reason or "fetch failed" in res.reason:
        outcome = "offline"
    else:
        outcome = "conflict"
    if not quiet:
        detail = res.context.get("detail", "")
        print(f"{Path(repo).name}: {outcome}"
              + (f" — {res.reason}" if not res.ok else "")
              + (f"\n  {detail.splitlines()[0]}" if detail else ""))
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description="sync space repos via ledger_transport")
    ap.add_argument("--repo", help="one space by directory name")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = ap.parse_args()

    repos = [d for d in sorted(args.root.glob("[0-9]-*"))
             if (d / ".git").exists() and (not args.repo or d.name == args.repo)]
    outcomes = [sync_repo(r, quiet=args.quiet) for r in repos]
    bad = [o for o in outcomes if o == "conflict"]
    if not args.quiet:
        print(f"\nspace_sync: {len(outcomes)} repo(s), {len(bad)} needing a human")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
