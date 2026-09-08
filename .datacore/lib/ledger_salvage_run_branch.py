#!/usr/bin/env python3
"""Merge a stranded nightshift run branch, re-chaining its ledger events.

WHY THIS EXISTS. Until 2026-09-08 a run appended to `.datacore/events/
<actor>.jsonl` on `nightshift/<date>` while the hourly cycle appended to the
SAME file on `main`: one append-only chain extended on two branches. Git
reports a content conflict, and no merge driver can repair it — a union
leaves two events claiming the same `prev`, which `verify_chain` rejects as
broken linkage. `resolve_ledger_conflicts.py` unions `*.jsonl` for exactly
this class and therefore produces a log that no longer verifies.

datacore#148 stops NEW forks (a run branch now writes `<actor>-run-<date>`).
This salvages the branches stranded before it: 7 of them across three spaces
on 2026-09-08, the oldest carrying 79 events.

WHAT IT DOES. Both sides of an append-only file keep the merge base as a
prefix (asserted, not assumed). So:

    main's file          stays canonical, untouched
    the branch's tail    becomes <actor>-run-<date>.jsonl, re-chained from
                         GENESIS with recomputed seq/prev/hash

Nothing is dropped: every event keeps its actor, type, payload and hlc, and
`read_events()` globs every `*.jsonl`, so a reader sees the same facts in the
same order. Re-chaining rewrites hashes, so it REFUSES a signed event rather
than silently invalidating a signature.

    ledger_salvage_run_branch.py <space> <branch>            # dry run
    ledger_salvage_run_branch.py <space> <branch> --apply    # merge + commit
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RUN_BRANCH = re.compile(r"nightshift/(\d{4}-\d{2}-\d{2})")


def git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def show(repo: Path, rev: str, path: str) -> list[str] | None:
    rc, out = git(repo, "show", f"{rev}:{path}")
    if rc != 0:
        return None
    return [l for l in out.splitlines() if l.strip()]


def default_branch(repo: Path) -> str:
    rc, out = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return out.strip().split("/")[-1] if rc == 0 and out.strip() else "main"


def rechain(events: list[dict]) -> list[dict]:
    """Rebuild an independent chain from GENESIS over the given event bodies."""
    from ledger.events import body_dict, compute_hash
    from ledger.verify import GENESIS

    out, prev = [], GENESIS
    for i, e in enumerate(events, start=1):
        body = body_dict(i, e["hlc"], e["actor"], e["type"], e.get("payload") or {}, prev)
        h = compute_hash(body)
        # `sig` is part of the on-disk record, not of the hashed body. An
        # unsigned event carries "" — omitting the key entirely makes
        # read_events raise CorruptLogError while verify_chain still passes,
        # so the file looks healthy and is unreadable.
        out.append({**body, "hash": h, "sig": ""})
        prev = h
    return out


def salvage(repo: Path, branch: str, apply: bool = False) -> int:
    db = default_branch(repo)
    m = RUN_BRANCH.search(branch)
    if not m:
        print(f"{repo.name}: {branch!r} is not a nightshift run branch", file=sys.stderr)
        return 2
    run_date = m.group(1)

    rc, base = git(repo, "merge-base", branch, db)
    if rc != 0:
        print(f"{repo.name}: no merge base with {db}", file=sys.stderr)
        return 2
    base = base.strip()

    rc, changed = git(repo, "diff", "--name-only", f"{base}..{branch}")
    logs = [p for p in changed.splitlines() if p.startswith(".datacore/events/") and p.endswith(".jsonl")]
    plan = []
    for path in logs:
        b_lines = show(repo, branch, path) or []
        base_lines = show(repo, base, path) or []
        main_lines = show(repo, db, path) or []
        if b_lines[:len(base_lines)] != base_lines:
            print(f"{repo.name}: {path} is not append-only against the base — refusing", file=sys.stderr)
            return 3
        tail = b_lines[len(base_lines):]
        if not tail:
            continue
        if main_lines[:len(base_lines)] != base_lines:
            print(f"{repo.name}: {db}'s {path} diverged from the base — refusing", file=sys.stderr)
            return 3
        events = [json.loads(l) for l in tail]
        signed = [e for e in events if e.get("sig")]
        if signed:
            print(f"{repo.name}: {path} has {len(signed)} SIGNED event(s); re-chaining would "
                  f"invalidate them — refusing", file=sys.stderr)
            return 4
        actor = events[0].get("actor") or Path(path).stem
        dest = f".datacore/events/{actor}-run-{run_date}.jsonl"
        plan.append((path, dest, events))

    if not plan:
        print(f"{repo.name}/{branch}: no forked writer log; a plain merge is enough")
    for src, dest, events in plan:
        print(f"{repo.name}/{branch}: {len(events)} event(s) from {src} -> {dest}")
        for t, n in sorted({e["type"]: 0 for e in events}.items()):
            print(f"    {sum(1 for e in events if e['type'] == t):>4}  {t}")
    if not apply:
        print("dry run — nothing written (pass --apply)")
        return 0

    rc, out = git(repo, "checkout", db)
    if rc != 0:
        print(f"{repo.name}: cannot check out {db}: {out.strip()[:200]}", file=sys.stderr)
        return 5
    git(repo, "merge", "--no-commit", "--no-ff", branch)   # conflicts expected

    for src, dest, events in plan:
        # main's shared log stays canonical; the branch tail moves to its own file.
        git(repo, "checkout", "--ours", "--", src)
        git(repo, "add", "--", src)
        body = "\n".join(json.dumps(e, separators=(",", ":"), sort_keys=True)
                         for e in rechain(events)) + "\n"
        (repo / dest).write_text(body, encoding="utf-8")
        git(repo, "add", "--", dest)

    rc, unresolved = git(repo, "diff", "--name-only", "--diff-filter=U")
    left = [u for u in unresolved.splitlines() if u.strip()]
    if left:
        print(f"{repo.name}: still conflicted, resolve by hand: {left[:5]}", file=sys.stderr)
        return 6

    rc, out = git(repo, "-c", "core.editor=true", "commit", "--no-edit")
    if rc != 0:
        print(f"{repo.name}: commit failed: {out.strip()[:200]}", file=sys.stderr)
        return 7

    from ledger.verify import verify_chain
    for _src, dest, _ in plan:
        res = verify_chain(repo / dest)
        ok = res[0] if isinstance(res, tuple) else bool(res)
        print(f"  verify {dest}: {'ok' if ok else 'FAILED'}")
        if not ok:
            return 8
    print(f"{repo.name}: merged {branch} into {db}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("space")
    ap.add_argument("branch")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    # Resolve against the DATA root, not this file: the script is often run
    # from a worktree of the system repo, which holds no space repos.
    import os
    repo = Path(a.space).expanduser()
    if not repo.is_absolute():
        root = Path(os.environ.get("DATACORE_ROOT") or (Path.home() / "Data"))
        repo = root / a.space
    return salvage(repo, a.branch, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
