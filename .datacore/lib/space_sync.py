#!/usr/bin/env python3
"""Self-healing space-repo sync for the Mac — no data loss, no silent failure.

Port of the box's proven cos_sync.sh pattern (see
.datacore/modules/chief-of-staff/server/lib/cos_sync.sh). Replaces the
stash -> pull -> stash pop recipe that stranded work in unlabelled stashes
whenever the pop conflicted — 10 orphaned stashes accumulated between
2026-05-14 and 06-29 before anyone noticed (2026-07-29 post-mortem,
ENG-2026-0729-009).

Per space repo ([0-9]-* under ~/Data):
  1. dirty?  -> autosave commit on the current branch (commit, never stash —
     a commit is on a branch, findable, pushable; a stash is invisible)
  2. fetch   -> on failure: warn and continue (offline is not an error state)
  3. rebase origin/<branch>
       clean -> push, report
       conflict -> abort, save local commits to mac-rescue-<TS> branch,
                   push the branch where possible, hard-reset to origin,
                   ALERT (stderr + macOS notification) — work is preserved
                   on a named, pushed branch, never destroyed

The root ~/Data repo is deliberately NOT touched: it is the user's working
copy (public OSS repo with commit hooks); auto-reset there could destroy
in-progress work. Sync it manually or via /today.

Usage:
    python3 .datacore/lib/space_sync.py               # all spaces
    python3 .datacore/lib/space_sync.py --repo 0-personal
    python3 .datacore/lib/space_sync.py --quiet
"""
import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_ROOT = Path.home() / "Data"


def run(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, f"timeout: {' '.join(args)}"


def notify(message: str) -> None:
    print(f"ALERT: {message}", file=sys.stderr)
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{message}" with title "Datacore sync"'],
        capture_output=True, timeout=10,
    )


def sync_repo(repo: Path, quiet: bool = False) -> str:
    """Sync one repo. Returns 'clean' | 'rescued' | 'offline' | 'skipped'."""
    name = repo.name

    def log(msg: str) -> None:
        if not quiet:
            print(f"{name}: {msg}")

    code, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    if code != 0 or branch == "HEAD":
        log("detached or unreadable HEAD — skipped, needs a human")
        return "skipped"

    # 1. Autosave: commit local work where it is findable, never stash it.
    run(["git", "add", "-A"], repo)
    code, _ = run(["git", "diff", "--cached", "--quiet"], repo)
    if code != 0:
        code, out = run(["git", "commit", "-m", "sync: mac autosave"], repo)
        if code != 0:
            # A commit hook rejected the autosave (e.g. date validation).
            # Leave the tree as it was and surface it — do NOT proceed to
            # rebase over a dirty tree.
            run(["git", "reset", "-q"], repo)
            notify(f"{name}: autosave commit rejected by hook — sync skipped, "
                   f"resolve manually ({out.splitlines()[-1][:80] if out else ''})")
            return "skipped"

    # 2. Fetch.
    code, _ = run(["git", "fetch", "origin", "-q"], repo, timeout=180)
    if code != 0:
        log("fetch failed (offline?) — local work is committed, will sync later")
        return "offline"

    # 3. Rebase; rescue on conflict.
    code, _ = run(["git", "rebase", f"origin/{branch}"], repo, timeout=180)
    if code == 0:
        run(["git", "push", "-q"], repo, timeout=180)
        code, head = run(["git", "rev-parse", "--short", "HEAD"], repo)
        log(f"synced clean (HEAD {head})")
        return "clean"

    run(["git", "rebase", "--abort"], repo)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    rescue = f"mac-rescue-{ts}"
    run(["git", "branch", rescue], repo)
    pushed = run(["git", "push", "-q", "origin", rescue], repo, timeout=180)[0] == 0
    run(["git", "reset", "--hard", f"origin/{branch}"], repo)
    notify(f"{name}: sync conflict — local work preserved on branch {rescue}"
           f"{' (pushed)' if pushed else ' (LOCAL ONLY — push failed)'}; "
           f"review and merge")
    return "rescued"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Sync only this space (e.g. 0-personal)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.repo:
        repos = [DATA_ROOT / args.repo]
    else:
        repos = sorted(p for p in DATA_ROOT.glob("[0-9]-*") if (p / ".git").exists())

    results: dict[str, int] = {}
    for repo in repos:
        if not (repo / ".git").exists():
            print(f"{repo.name}: not a git repo — skipped", file=sys.stderr)
            continue
        outcome = sync_repo(repo, quiet=args.quiet)
        results[outcome] = results.get(outcome, 0) + 1

    summary = " ".join(f"{k}={v}" for k, v in sorted(results.items()))
    print(f"space_sync: {summary}")
    # Rescues are preserved work, not failures; only a repo we could not
    # handle at all (skipped) is worth a non-zero exit.
    return 1 if results.get("skipped") else 0


if __name__ == "__main__":
    sys.exit(main())
