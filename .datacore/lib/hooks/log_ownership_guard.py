#!/usr/bin/env python3
"""Single-writer log ownership, enforced where GitHub cannot run a hook.

DIP-0046 D5 puts a `pre-receive` on the Gitea repos. github.com does not run
custom server-side hooks at all, so five of nine spaces — 1-datafund,
2-datacore, 3-fds, 5-plur, 8-firm — have no server-side equivalent. D6's
rulesets close force-push and deletion there, but rulesets cannot express
"actor X may only write path Y", so this is the layer that can.

It enforces the invariant the whole transport rests on:

    an actor appends only to `.datacore/events/<itself>.jsonl`

Per-writer logs being disjoint files is the ENTIRE reason a merge is a union
that cannot conflict. One actor writing another's log breaks that silently: the
merge still succeeds, the fold still runs, and events are attributed to someone
who never emitted them. Nothing downstream notices, which is why it needs
catching at the push.

Client-side, so it is bypassable with --no-verify — deliberately, because a
human resolving a genuine mess sometimes must. Two things make that acceptable:
`core.hooksPath` is watched by detectors/config_drift.py, so a machine that
quietly drops its hooks is reported; and this refuses on the ONE thing an
automated actor never legitimately does, so a bypass is a considered human act
rather than routine.

Reads membership from `<space>/.datacore/members.yaml` when present. Absent, it
still enforces ownership: a space with no membership file predates D5, and
refusing every push there would be an outage rather than a check.

    log_ownership_guard.py <range>...      ranges as given to pre-push
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ACTOR_LOG = re.compile(r"^(?:.*/)?\.datacore/events/([A-Za-z0-9_-]+)\.jsonl$")

# `genesis` is a ROLE, not a machine: it is the import actor, and
# `ledger_ingest_org.py` appends to it from whichever machine runs the sweep.
# Refusing that blocks every ordinary import.
#
# Worth stating rather than hiding: this is therefore the ONE log the
# disjoint-writer argument does not cover. If two machines ever ran the ingest
# sweep concurrently they would both append to genesis.jsonl and could produce
# exactly the interleaving the per-writer design exists to prevent. Today only
# the Mac is scheduled for it (mac-ledger-ingest, 07:40). If that ever changes,
# the importer needs a per-machine log — genesis-<machine>.jsonl — not an
# exemption here.
SHARED_ROLE_LOGS = {"genesis"}


def actor() -> str:
    """Same resolution as ledger_cli: $DATACORE_ACTOR, else hostname.

    Lower-cased: this machine's hostname is "Mac" while its log is `mac.jsonl`,
    so a case-sensitive compare made the guard report mac writing its OWN log.
    """
    return (os.environ.get("DATACORE_ACTOR")
            or socket.gethostname().split(".")[0]).lower()


def git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "")


def changed(rng: str) -> list[str]:
    """Files touched by LOCALLY AUTHORED commits in this range.

    `--no-merges` is load-bearing. A converge fetches other actors' logs and
    merges them; the merge commit then shows those logs as "changed" relative
    to its first parent, so a plain `git diff <range>` reported this machine as
    writing genesis.jsonl and blocked every ordinary sync. Commits that came
    from origin are already on origin and so are not in the range at all —
    what remains, minus merges, is what this machine actually wrote.
    """
    rc, out = git("rev-list", "--no-merges", rng)
    if rc != 0:
        return []
    files: list[str] = []
    for sha in out.split():
        rc2, names = git("show", "--name-only", "--format=", sha)
        if rc2 == 0:
            files.extend(l for l in names.splitlines() if l.strip())
    return files


def members(root: Path) -> list[str]:
    p = root / ".datacore" / "members.yaml"
    if not p.is_file():
        return []
    out, in_list = [], False
    for line in p.read_text(errors="replace").splitlines():
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


def main(argv: list[str]) -> int:
    if not argv:
        return 0
    me = actor()
    rc, top = git("rev-parse", "--show-toplevel")
    root = Path(top.strip()) if rc == 0 and top.strip() else Path.cwd()

    foreign: set[str] = set()
    for rng in argv:
        for f in changed(rng):
            m = ACTOR_LOG.match(f)
            if m and m.group(1).lower() != me \
                    and m.group(1).lower() not in SHARED_ROLE_LOGS:
                foreign.add(m.group(1))

    if not foreign:
        return 0

    allowed = members(root)
    print(f"\ndatacore/pre-push REFUSED: {me!r} modified another actor's event log:",
          file=sys.stderr)
    for a in sorted(foreign):
        print(f"  .datacore/events/{a}.jsonl", file=sys.stderr)
    print("\nPer-writer logs are disjoint files, which is the entire reason a merge\n"
          "is a union that cannot conflict. Writing another actor's log breaks that\n"
          "silently — the merge succeeds and the events are attributed to someone\n"
          "who never emitted them.\n", file=sys.stderr)
    if allowed:
        print(f"Members of this space: {', '.join(allowed)}", file=sys.stderr)
    print("If you are deliberately repairing a log by hand, bypass with:\n"
          "  SKIP_PRE_PUSH=1 git push ...   (or --no-verify)\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
