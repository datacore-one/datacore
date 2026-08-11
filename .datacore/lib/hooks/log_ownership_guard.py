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


def actor() -> str:
    """Same resolution as ledger_cli: $DATACORE_ACTOR, else hostname."""
    return os.environ.get("DATACORE_ACTOR") or socket.gethostname().split(".")[0]


def git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "")


def changed(rng: str) -> list[str]:
    rc, out = git("diff", "--name-only", rng)
    return [l for l in out.splitlines() if l.strip()] if rc == 0 else []


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
            if m and m.group(1) != me:
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
