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


_RUN_SUFFIX = re.compile(r"-run-\d{4}-\d{2}-\d{2}$")


def base_writer(name: str) -> str:
    """The canonical writer behind a branch-scoped log name (datacore#148).

    Kept local rather than imported from actor_identity: this runs as a git
    hook, where an import failure would block every push in the fleet."""
    return _RUN_SUFFIX.sub("", (name or "").strip().lower())


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


class UnlistableRange(Exception):
    """`git rev-list` could not list a pushed range, so it cannot be checked."""


def changed(rng: str) -> list[str]:
    """Files written by the commits of this range that NO remote has yet.

    Decision S3 (2026-09-23): the guard judges every commit in the pushed range
    that no remote-tracking ref contains, whatever its author email says. Until
    then it judged only commits whose author email was this machine's, and an
    author email is whatever `git -c user.email=…` says: a commit written here
    under another actor's email passed (DatacoreSpec/Guards.lean
    `author_filter_is_forgeable`, now `unpushed_writes_are_judged`).

    "Not on any remote" is what keeps honest merge-based sync (DIP-0046)
    working. A converge fetches other actors' commits before merging them, so
    they sit on a remote-tracking ref and are excluded: they are carried, not
    written. On 2026-08-13 two Winston-authored commits in Miles's push range
    blocked his wrap-up; those commits came from origin, so they are excluded
    here too. A commit that reached this repo by any path other than a fetch
    (a local branch, a patch, a forged email) is judged as this machine's.

    Per-commit, never `git diff <range>`: a merge's diff against its first
    parent lists the logs it carried in. Merges ARE inspected, but only for
    what the merge itself wrote. `git show --cc --name-only` on a merge is a
    combined diff: it lists a path only when the result differs from EVERY
    parent. A clean union merge lists nothing; a merge that edits another
    actor's log while resolving it (an "evil merge") lists that log
    (2026-09-23, `ownership_sees_merge_writes`).

    Decision S4 (2026-09-23): a range `git rev-list` cannot list raises
    UnlistableRange, and main() refuses the push. An unevaluable range is not
    a clean one (`unlistable_range_refuses`).
    """
    rc, out = git("rev-list", rng, "--not", "--remotes")
    if rc != 0:
        raise UnlistableRange(rng)
    files: list[str] = []
    for sha in out.split():
        rc2, names = git("show", "--cc", "--name-only", "--format=", sha)  # --cc pins the combined diff whatever log.diffMerges says
        if rc2 != 0:
            raise UnlistableRange(f"{rng} (git show {sha[:12]} failed)")
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
        try:
            files = changed(rng)
        except UnlistableRange as exc:
            print(f"\ndatacore/pre-push REFUSED: the ownership guard could not list {exc.args[0]!r}\n"
                  "(git rev-list failed), so this range was NOT checked, and an unchecked\n"
                  "range is not pushed. Fetch, check the refs exist, and push again.\n"
                  "If you are deliberately pushing anyway:\n"
                  "  SKIP_PRE_PUSH=1 git push ...   (or --no-verify)\n", file=sys.stderr)
            return 1
        for f in files:
            m = ACTOR_LOG.match(f)
            if not m:
                continue
            # A branch-scoped log (`<actor>-run-<date>.jsonl`, datacore#148) is
            # the SAME writer on a run branch, not a stranger. The filename is
            # the file, not the identity — the third place that conflated them,
            # after principal_of() and the seq high-water mark. Resolve through
            # base_writer() so the guard keeps catching a genuine foreign log.
            who = base_writer(m.group(1))
            if who not in mine and who not in SHARED_ROLE_LOGS:
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
