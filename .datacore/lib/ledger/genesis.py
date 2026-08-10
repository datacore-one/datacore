"""Genesis import (DIP-0043): fold the EXISTING org corpus into the ledger.

This is the migration. Everything else in v2 built a parallel system; this is
the step where the tasks you already have become ledger items.

Three decisions make it safe to run, and to re-run:

IDENTITY IS THE ORG `:ID:`. Every active task already carries one
(org-workspace's `ensure-ids` guarantees it -- verified 2026-08-10: 1212 of
1212 active tasks across nine spaces). Reusing it verbatim means identity
survives the boundary, nothing is remapped, and the import is IDEMPOTENT by
construction: a second run finds every id already in the fold and emits
nothing. That property is why this can be run before anyone is confident in
it.

VALID TIME COMES FROM A LADDER, NEVER FROM NOW. `:CREATED:` if the task has
one, else the task's first appearance in git history, else a single documented
genesis timestamp. A task created in March must not enter the ledger claiming
it happened at import time -- that would put the whole corpus at one instant
and destroy any ordering the history had.

SCOPE IS ACTIVE WORK. TODO / NEXT / WAITING. Archives stay files: they are
historical, not coordinated, and importing them would multiply the corpus for
no coordination benefit. Overlay states (nightshift's QUEUED/WORKING/REVIEW/
FAILED) are OUT of scope and reported, not silently skipped -- see
`scan()`'s `out_of_scope`.

The payload carries the whole task because a projector has to rebuild the
heading byte-for-byte: first-class fields the fold indexes, plus an `org`
sub-dict holding what only org-mode cares about.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .events import Event  # noqa: F401  (re-exported for callers/tests)
from .fold import fold
from .log import EventLog, read_events

#: Human workflow states.
HUMAN_STATES = ("TODO", "NEXT", "WAITING", "DEFERRED")

#: Nightshift's execution overlay (DIP-0011). These were originally excluded,
#: which left 87 tasks unmigratable and blocked the whole migration on
#: rewriting nightshift's write path first. That was the wrong call: a task in
#: REVIEW is not finished, it is live work wearing an execution badge. Excluding
#: it would have silently dropped 7% of the corpus at the boundary -- the exact
#: "migration loses work quietly" failure this import is written to avoid.
#: The overlay state is preserved verbatim in the payload, so the projection
#: renders it back unchanged and nightshift keeps seeing what it expects.
OVERLAY_STATES = ("QUEUED", "WORKING", "REVIEW", "FAILED")

#: Everything that represents live, coordinated work. DONE and CANCELLED are
#: the only states that stay behind as history.
ACTIVE_STATES = HUMAN_STATES + OVERLAY_STATES

#: Used only when a task has no `:CREATED:` and no git history -- e.g. a task
#: created in an uncommitted edit. Documented rather than invented per task so
#: every such item shares one obvious, greppable timestamp.
GENESIS_FALLBACK = "1970-01-01"


@dataclass
class ScanResult:
    """What an import WOULD do, computed without writing anything."""

    importable: list[dict] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    missing_id: list[str] = field(default_factory=list)
    out_of_scope: dict[str, int] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        oos = ", ".join(f"{k}={v}" for k, v in sorted(self.out_of_scope.items())) or "none"
        return (
            f"importable={len(self.importable)} "
            f"already_present={len(self.already_present)} "
            f"missing_id={len(self.missing_id)} out_of_scope[{oos}]"
        )


def _git_first_seen(repo: Path, rel: str, needle: str) -> str | None:
    """Date a string first appears in a file's git history, or None.

    Rung two of the valid-time ladder. Best-effort by design: a space that is
    not a git repo, or a task added in an uncommitted edit, simply falls
    through to the next rung rather than failing the import.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--reverse", "--format=%ad",
             "--date=short", "-S", needle, "--", rel],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def valid_time(node, repo: Path | None = None, rel: str | None = None) -> tuple[str, str]:
    """`(date, rung)` for a task -- the ladder, with its rung named.

    The rung is returned, not just the date, so an operator reading the import
    can tell a task whose creation date is KNOWN from one whose date was
    inferred from git or defaulted. A date with no provenance is worse than no
    date, because it looks equally authoritative.
    """
    created = node.get_property("CREATED")
    if created:
        cleaned = created.strip().strip("[]<>").split()[0]
        if cleaned:
            return cleaned, "created_property"
    if repo is not None and rel is not None:
        node_id = node.get_property("ID")
        if node_id:
            seen = _git_first_seen(repo, rel, node_id)
            if seen:
                return seen, "git_history"
    return GENESIS_FALLBACK, "genesis_fallback"


def task_payload(node, space: str, date: str, rung: str) -> dict:
    """One task as an `item.create` payload.

    First-class fields are what the ledger indexes and what two actors could
    conflict over. Everything org-only goes under `org` -- kept whole so the
    projector can rebuild the heading without the ledger schema having to
    grow a field per org feature.
    """
    # SORTED, and this is load-bearing: org-workspace returns tags as a SET,
    # so join order varies across processes under hash randomisation. An
    # unsorted join makes the projection non-deterministic between machines --
    # the exact "set iteration order" determinism killer DIP-0043 names. Caught
    # 2026-08-10 by a full-suite run rendering ":urgent:work:" where a
    # single-file run rendered ":work:urgent:".
    tags = sorted(t for t in (node.tags or []) if t)
    return {
        "id": node.get_property("ID"),
        "title": node.heading,
        "state": node.todo,
        "space": space,
        "scheduled": str(node.scheduled) if node.scheduled else None,
        "deadline": str(node.deadline) if node.deadline else None,
        "tags": tags,
        "org": {
            "priority": node.priority,
            "body": node.body or "",
            "properties": {
                k: v for k, v in (node.properties or {}).items()
                if k not in ("ID", "CREATED")
            },
        },
        "genesis": {"date": date, "rung": rung},
        #: True for nightshift's execution overlay. Carried so a consumer can
        #: tell "the human parked this" from "a machine is mid-flight on it"
        #: without re-deriving it from the state string.
        "overlay": node.todo in OVERLAY_STATES,
    }


def scan(space_dir: Path, org_file: Path | None = None) -> ScanResult:
    """What would be imported, computed WITHOUT writing.

    Folds the existing ledger once up front so `already_present` reflects a
    single consistent snapshot -- the same discipline `materialize()` uses.
    """
    import sys

    lib = Path(__file__).resolve().parents[1]
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from org_workspace import OrgWorkspace

    org_file = org_file or (space_dir / "org" / "next_actions.org")
    result = ScanResult()
    if not org_file.exists():
        return result

    known = set(fold(read_events(space_dir)).items.keys())

    ws = OrgWorkspace()
    ws.load(str(org_file))
    repo, rel = space_dir, str(org_file.relative_to(space_dir))
    space = space_dir.name

    for node in ws.all_nodes():
        state = node.todo
        if not state:
            continue
        if state not in ACTIVE_STATES:
            result.out_of_scope[state] = result.out_of_scope.get(state, 0) + 1
            continue
        node_id = node.get_property("ID")
        if not node_id:
            result.missing_id.append(node.heading[:80])
            continue
        if node_id in known:
            result.already_present.append(node_id)
            continue
        date, rung = valid_time(node, repo, rel)
        result.importable.append(task_payload(node, space, date, rung))

    return result


def import_space(space_dir: Path, org_file: Path | None = None,
                 actor: str = "genesis", dry_run: bool = False) -> ScanResult:
    """Run the import. Idempotent: re-running imports nothing new.

    Writes to a dedicated `genesis` writer file so imported items are
    attributable and separable from anything an agent later does to them.
    """
    result = scan(space_dir, org_file)
    if dry_run or not result.importable:
        return result

    log = EventLog(space_dir, actor)
    for payload in result.importable:
        log.append("item.create", payload)
    return result
