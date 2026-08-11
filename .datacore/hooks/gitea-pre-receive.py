#!/usr/bin/env python3
"""Server-side membership + log-ownership check (DIP-0046 D5).

Client hooks are advice. `core.hooksPath` is a local setting, an agent can
unset it, and `git clone` never copies hooks at all — Data's clone was created
with zero enforcement from birth and nothing noticed. The server is the only
place a rule cannot be opted out of, so the two invariants that actually matter
are checked here:

  MEMBERSHIP     the pusher appears in `<space>/.datacore/members.yaml`
  LOG OWNERSHIP  a push may only modify `.datacore/events/<actor>.jsonl` for
                 actors it is allowed to write as — normally just its own

Ownership is the load-bearing one. Per-writer logs are disjoint files, and that
disjointness is the ENTIRE reason a merge is a union that cannot conflict. One
actor appending to another's log breaks the property the whole transport rests
on, and it does so silently: the merge still succeeds, the fold still runs, and
the events are simply attributed to someone who never emitted them.

Read from the INCOMING commit, never from a worktree — a bare repo has none.
`git cat-file` against the pushed sha also means the members list is evaluated
as the push itself defines it, so adding yourself and using the privilege in
one push is visible as exactly that.

REPORT-ONLY BY DEFAULT. Set DATACORE_ENFORCE=1 to reject. `0-personal` is the
operator's own daily space and a pre-receive rejection cannot be bypassed from
the client, so a wrong rule here locks them out of their own notes. It runs
silent-but-logging until the log has been quiet against real pushes.

stdin: "<old> <new> <ref>" per line. Exit 0 always unless enforcing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ACTOR_LOG = re.compile(r"^(?:.*/)?\.datacore/events/([A-Za-z0-9_-]+)\.jsonl$")
MEMBERS = ".datacore/members.yaml"
ZERO = "0" * 40


def git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def members_at(sha: str) -> list[str]:
    """Membership as the pushed commit defines it. Absent file -> no list."""
    rc, blob = git("cat-file", "-p", f"{sha}:{MEMBERS}")
    if rc != 0:
        return []
    # Deliberately not yaml.safe_load: the Gitea server is not ours to add
    # dependencies to, and this file's shape is a flat list we control.
    out, in_list = [], False
    for line in blob.splitlines():
        if line.startswith("members:"):
            in_list = True
            continue
        if in_list:
            m = re.match(r"\s*-\s*(\S+)", line)
            if m:
                out.append(m.group(1))
            elif line.strip() and not line.startswith((" ", "-")):
                break
    return out


def changed_files(old: str, new: str) -> list[str]:
    rng = [new] if old == ZERO else [f"{old}..{new}"]
    rc, out = git("diff", "--name-only", *rng)
    return [l for l in out.splitlines() if l.strip()] if rc == 0 else []


def pusher() -> str:
    """Gitea sets GITEA_PUSHER_NAME; fall back to the ssh key's user."""
    for var in ("DATACORE_ACTOR", "GITEA_PUSHER_NAME", "GL_USERNAME", "USER"):
        v = os.environ.get(var)
        if v:
            return v
    return "unknown"


def main() -> int:
    actor = pusher()
    enforce = os.environ.get("DATACORE_ENFORCE") == "1"
    violations: list[str] = []   # block when enforcing
    warnings: list[str] = []     # never block, whatever the mode

    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 3:
            continue
        old, new, ref = parts
        if new == ZERO:      # branch deletion carries no tree to inspect
            continue

        allowed = members_at(new)
        files = changed_files(old, new)
        touched = {m.group(1) for f in files if (m := ACTOR_LOG.match(f))}

        # No members.yaml -> the space predates D5. Report, never reject:
        # rejecting every push to an unmigrated space is an outage, not a check.
        # A WARNING, never a violation. An unmigrated space has no list to check
        # against, so there is nothing to be guilty of — and rejecting every push
        # to it is an outage dressed as a security control. Verified: an earlier
        # revision put this in `violations` and enforce-mode rejected a space
        # whose only fault was not having been migrated yet.
        if not allowed:
            if touched:
                warnings.append(f"{ref}: no {MEMBERS} in tree (unmigrated space)")
            continue

        if actor not in allowed and touched:
            violations.append(
                f"{ref}: pusher {actor!r} not in {MEMBERS} ({', '.join(allowed)})")
        for other in sorted(touched - {actor}):
            violations.append(
                f"{ref}: {actor!r} modified {other}.jsonl — logs are single-writer")

    for w in warnings:
        print(f"datacore/warn: {w}", file=sys.stderr)
    if violations:
        tag = "REJECT" if enforce else "would reject"
        for v in violations:
            print(f"datacore/{tag}: {v}", file=sys.stderr)
        if enforce:
            print("\nSee DIP-0046 §11. Append to your own log, or add yourself to "
                  f"{MEMBERS} in a separate reviewed commit.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
