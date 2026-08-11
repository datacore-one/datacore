#!/usr/bin/env python3
"""Which facts exist here but not on the remote — the drain metric (DIP-0046 A1).

Every actor's log is an append-only chain whose events carry a monotonic `seq`.
Comparing the local head seq against the remote's answers, per actor, "how many
facts has this machine written that nobody else can see?"

That number is the one this installation kept failing to know. 610 commits sat
on a parked branch for two months; 645 more accumulated across 74 unmerged
branches five days running; a `git push … || true` reported "synced clean" while
pushing nothing. Each was invisible for the same reason: the question "is my work
anywhere but this disk?" had no cheap answer, so nothing asked it.

This is deliberately the CHEAPEST possible form of that question. It reads
`git show <remote-ref>:<path>` and the local file — no fold, no chain
verification, no network beyond what a fetch already did. It works under the
current design and under DIP-0046's target design unchanged, which is why it is
built first: a detector that outlives the mechanism it watches is worth more than
one that has to be rewritten alongside it.

What it deliberately does NOT do:

  - It does not fetch. A detector that mutates refs changes the thing it
    measures, and a stale fetch understates a gap rather than inventing one.
    Freshness is the caller's job (`--fetch` if you want it).
  - It does not verify the chain. `ledger_cli.py verify` owns that. Conflating
    "unpushed" with "corrupt" would make one alarm mean two things.
  - It does not treat a missing remote file as zero. A log the remote has never
    seen is a gap of its entire length, which is the loudest case, not the
    quietest.

Exit 0 when every actor is fully published, 1 when any gap exists, 2 on error.

    seq_gap.py [--root DIR] [--space NAME] [--fetch] [--json]
"""
from __future__ import annotations

import argparse
import os
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> tuple[int, str]:
    """(returncode, stdout). Never raises — a git failure is data here."""
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, timeout=60)
        return r.returncode, (r.stdout or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def default_branch(repo: Path) -> str:
    """The repo's default branch from origin/HEAD, falling back to main.

    Same resolution as git_fleet_audit.default_branch. The fallback matters: an
    archive repo with origin/HEAD unset is master-based, and assuming 'main'
    there makes every ref lookup fail — which must read as an ERROR, never as
    'no gap'. See `head_seq` below.
    """
    rc, out = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    out = out.strip()
    return out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else "main"


def head_seq(text: str) -> int | None:
    """Highest `seq` in a JSONL log, or None if the log has no readable events.

    Scans from the end and stops at the first parseable line, so a torn final
    line — the exact hazard atomic publish (DIP-0046 §10) exists to remove —
    degrades to "the last complete event" instead of crashing the detector.
    """
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            seq = json.loads(line).get("seq")
        except ValueError:
            continue          # torn or corrupt line: fall back to the one before
        if isinstance(seq, int):
            return seq
    return None


def scan_space(space: Path, *, fetch: bool = False) -> list[dict]:
    """One row per actor log in this space."""
    events_dir = space / ".datacore" / "events"
    if not events_dir.is_dir():
        return []
    if fetch:
        git(space, "fetch", "-q", "--prune")

    db = default_branch(space)
    rows = []
    for log in sorted(events_dir.glob("*.jsonl")):
        actor = log.stem
        rel = log.relative_to(space).as_posix()
        local = head_seq(log.read_text(errors="replace"))

        rc, out = git(space, "show", f"origin/{db}:{rel}")
        if rc != 0:
            # Distinguish the two reasons `show` fails. A log the remote has
            # never seen is the LOUDEST gap; an unresolvable ref is an error we
            # must not report as agreement.
            rc_ref, _ = git(space, "rev-parse", "--verify", f"origin/{db}")
            if rc_ref != 0:
                rows.append({"space": space.name, "actor": actor, "local_seq": local,
                             "remote_seq": None, "gap": None,
                             "error": f"cannot resolve origin/{db}"})
                continue
            remote = None
        else:
            remote = head_seq(out)

        if local is None:
            gap = 0 if remote is None else None
        elif remote is None:
            gap = local + 1                      # nothing published at all
        else:
            gap = max(0, local - remote)
        rows.append({"space": space.name, "actor": actor, "local_seq": local,
                     "remote_seq": remote, "gap": gap, "error": None})
    return rows


def _default_root() -> Path:
    """Root from DATACORE_ROOT, then ~/Data — NEVER from this file's location.

    A second checkout exists for scheduled runs (~/.datacore/v2-runner). A
    location-derived root would make this scan THAT tree, which holds zero
    spaces, and report "0 findings" — a false green, and the same defect
    seq_gap shipped once already as a parents[] off-by-one.
    """
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def main() -> int:
    ap = argparse.ArgumentParser()
    # parents: detectors -> lib -> .datacore -> <data root>. Off-by-one here
    # silently scans nothing and reports "0 logs, 0 gaps", which is the
    # detector's own version of a green light meaning nothing.
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--space", help="limit to one space by directory name")
    ap.add_argument("--fetch", action="store_true", help="fetch before comparing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    spaces = [d for d in sorted(args.root.glob("[0-9]-*"))
              if (d / ".git").exists() and (not args.space or d.name == args.space)]

    # SCANNING NOTHING IS NOT A PASS — this detector already shipped that bug
    # once, as a parents[] off-by-one that reported "0 logs, 0 gaps" while
    # examining an empty directory. Pointed at the wrong root it would do it
    # again, silently, and the contract above it would stay green.
    if not spaces:
        print(f"ERROR: no space repos under {args.root} — refusing to report clean")
        return 2

    rows: list[dict] = []
    for sp in spaces:
        rows.extend(scan_space(sp, fetch=args.fetch))

    errors = [r for r in rows if r["error"]]
    gaps = [r for r in rows if r["gap"]]

    if args.json:
        print(json.dumps({"rows": rows, "gaps": len(gaps), "errors": len(errors)}, indent=2))
    else:
        for r in rows:
            if r["error"]:
                print(f"  ERROR {r['space']}/{r['actor']}: {r['error']}")
            elif r["gap"]:
                print(f"  GAP   {r['space']}/{r['actor']}: local seq {r['local_seq']}, "
                      f"remote {r['remote_seq']} — {r['gap']} unpublished")
            else:
                print(f"  ok    {r['space']}/{r['actor']}: seq {r['local_seq']} published")
        # Report positively: a count nobody can mistake for "the detector ran and
        # found nothing" versus "the detector did not run" (DIP-0046 §8).
        print(f"\nseq-gap: {len(rows)} log(s), {len(gaps)} with unpublished events, "
              f"{len(errors)} error(s)")

    if errors:
        return 2
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
