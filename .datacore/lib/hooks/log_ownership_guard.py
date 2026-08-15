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


def _registry_actors(root: Path, host: str) -> list[str]:
    """Which ledger actors this MACHINE may write, per the registry.

    A hostname is not an actor name. winston's hostname is `chief-of-staff`
    while its ledger actor is `winston`, so a hostname-derived actor made the
    guard refuse winston writing its OWN log — blocking every push from the
    Chief of Staff box. The registry already recorded the mapping
    (servers.winston.ledger_actors); this reads it instead of assuming.
    """
    try:
        import yaml
        reg = yaml.safe_load((root / ".datacore/registry/infrastructure.yaml").read_text())
    except Exception:      # noqa: BLE001 — no registry is not a violation
        return []
    servers = (reg or {}).get("servers") or {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        access = cfg.get("access") or {}
        if host in (name.lower(),
                    str(access.get("hostname", "")).lower(),
                    str(cfg.get("ssh_alias", "")).lower(),
                    str(cfg.get("manifest_machine", "")).lower()):
            return [str(a).lower() for a in (cfg.get("ledger_actors") or [])]
    return []


def _data_root() -> Path:
    """Find the Data root from a SPACE repo, which does not contain the registry."""
    env = os.environ.get("DATACORE_ROOT")
    if env:
        return Path(env)
    here = Path.cwd().resolve()
    for cand in [here, *here.parents]:
        if (cand / ".datacore" / "registry" / "infrastructure.yaml").is_file():
            return cand
    return Path.home() / "Data"


def actors() -> list[str]:
    """Every actor identity this machine may legitimately write.

    Registry-first: when the infrastructure registry maps this host to
    specific ledger_actors, that mapping is authoritative and DATACORE_ACTOR
    is ignored for ownership checks. DATACORE_ACTOR=winston on the mac would
    otherwise make the guard allow the mac to write to winston.jsonl — exactly
    what caused the 2026-08-12 chain fork (resolved in merge 221efd0).

    DATACORE_ACTOR still controls which actor the ledger_transport writes as
    at runtime; the guard only determines which files this machine may push.

    Lower-cased: this Mac's hostname is "Mac" while its log is `mac.jsonl`, so a
    case-sensitive compare made the guard report mac writing its OWN log.
    """
    host = socket.gethostname().split(".")[0].lower()
    registry = _registry_actors(_data_root(), host)
    if registry:
        # Registry is the ground truth; DATACORE_ACTOR cannot override it.
        return sorted({host, *registry})
    # No registry entry for this host — fall back to DATACORE_ACTOR or hostname.
    explicit = os.environ.get("DATACORE_ACTOR")
    if explicit:
        return [explicit.lower()]
    return [host]


def actor() -> str:
    """Primary identity, for messages."""
    return actors()[0]


def git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "")


def local_identity() -> str:
    """This machine's git author email — the only reliable "who wrote it".

    Actor names are not usable here: Winston commits as "Winston (CoS)", Miles
    as "Miles", and the registry knows neither string. The email is what git
    stamps on every commit and what each box configures once.
    """
    rc, out = git("config", "user.email")
    return out.strip().lower() if rc == 0 else ""


def changed(rng: str) -> list[str]:
    """Files touched by commits THIS MACHINE AUTHORED in this range.

    `--no-merges` is load-bearing. A converge fetches other actors' logs and
    merges them; the merge commit then shows those logs as "changed" relative
    to its first parent, so a plain `git diff <range>` reported this machine as
    writing genesis.jsonl and blocked every ordinary sync.

    But --no-merges alone is NOT enough, and the assumption it rested on —
    "commits from origin are already on origin and so are not in the range" —
    is false once the fleet stopped rebasing. A merge carries other actors'
    commits into your history AS THEMSELVES, so a push range legitimately
    contains foreign-authored commits that have not reached this remote yet.
    Rebase used to hide that by replaying everything under the pusher.

    On 2026-08-13 that blocked Miles's entire nightshift wrap-up: two commits
    authored by Winston, touching winston.jsonl, sat in Miles's push range, and
    the guard reported Miles as having written another actor's log. The events
    were Winston's, correctly attributed, doing exactly what merge-based sync
    is supposed to do.

    So filter by AUTHOR. What this machine is accountable for is what it wrote,
    not what it is carrying. Anything else is someone else's commit in transit,
    and blaming the courier both blocks honest work and — worse — trains
    everyone to reach for SKIP_PRE_PUSH, which disables the check for the real
    case it exists to catch.
    """
    me = local_identity()
    rc, out = git("rev-list", "--no-merges", rng)
    if rc != 0:
        return []
    files: list[str] = []
    for sha in out.split():
        if me:
            rc_a, author = git("show", "-s", "--format=%ae", sha)
            if rc_a == 0 and author.strip().lower() != me:
                continue          # someone else's commit, merely passing through
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
    mine = actors()
    me = "/".join(mine)
    rc, top = git("rev-parse", "--show-toplevel")
    root = Path(top.strip()) if rc == 0 and top.strip() else Path.cwd()

    foreign: set[str] = set()
    for rng in argv:
        for f in changed(rng):
            m = ACTOR_LOG.match(f)
            if m and m.group(1).lower() not in mine \
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
