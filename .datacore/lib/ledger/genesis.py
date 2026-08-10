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


def _parent_id(node) -> str | None:
    """The IMMEDIATE parent's org :ID:, or None.

    DIP-0043 makes parent mandatory: org tasks are hierarchical and a flat
    projection cannot rebuild the tree.

    Deliberately the immediate parent only. An earlier version walked up to the
    nearest ID-bearing ancestor so that a task under a plain section heading
    would still attach "somewhere" -- but that invents a parent-child edge the
    file never had, and org-mode tag inheritance then makes it visible: a firm
    task came back carrying `infra` and `security` from a grandparent it was
    never under. A task whose immediate parent is a plain heading genuinely has
    no task parent; its position is carried by `level` instead.
    """
    parent = getattr(node, "parent", None)
    if parent is None:
        return None
    try:
        return parent.get_property("ID") or None
    except Exception:  # noqa: BLE001 — a root/sentinel node has no properties
        return None


def _section_payload(node, space: str) -> dict:
    """A non-task heading that a task lives under, as a structural item.

    `ensure-ids` gives EVERY heading an id, including plain section headings
    like `* Operations`. They are legitimate parents, but they carry no todo
    state, so an import that only takes tasks skips them -- and their children
    are then re-parented under whatever task precedes them, inheriting its
    tags. Observed 2026-08-10: a Telegram task under `* Operations` came back
    under an infra/security task and inherited both.

    Imported with `state: None` and `section: True` so the projector renders a
    plain heading rather than a TODO, and so a consumer can filter them out
    when it wants tasks only.
    """
    own = getattr(node, "shallow_tags", None)
    tags = sorted(t for t in (own if own is not None else (node.tags or [])) if t)
    return {
        "id": node.get_property("ID"),
        "title": node.heading,
        "state": None,
        "section": True,
        "space": space,
        "tags": tags,
        #: The task's EFFECTIVE tags in the source file, inheritance included.
        #: Normally redundant -- the projection rebuilds the tree, so org
        #: re-derives them. But a task whose parent is DONE has no parent in
        #: the projection, so it silently loses whatever it inherited from it.
        #: Kept so a promoted orphan can carry its tags explicitly instead.
        "effective_tags": sorted(t for t in (node.tags or []) if t),
        "level": getattr(node, "level", None),
        "parent": _parent_id(node),
        "org": {"priority": None, "body": "", "properties": {}},
        "genesis": {"date": GENESIS_FALLBACK, "rung": "section"},
    }


def _outline(node) -> list[dict]:
    """Ancestor headings that are NOT tasks, outermost first.

    org-mode tag inheritance follows a file's PHYSICAL nesting, not any link
    we record. A task living under `* Infrastructure :infra:security:`
    inherits those tags; drop that heading from the projection and the task
    lands under whatever level-1 task happens to precede it and inherits ITS
    tags instead. Observed 2026-08-10: 571 of 574 tasks in 0-personal came
    back with tags they never had.

    So the scaffold is part of the data. Captured per task (rather than as
    separate section items) because a heading has no id to key on, and this
    keeps every task self-describing: its payload alone says where it lives.
    """
    chain, cur = [], getattr(node, "parent", None)
    while cur is not None:
        try:
            has_id = bool(cur.get_property("ID"))
            heading = cur.heading
            level = cur.level
            own = getattr(cur, "shallow_tags", None)
            tags = sorted(t for t in (own if own is not None else (cur.tags or [])) if t)
        except Exception:  # noqa: BLE001 — root/sentinel node
            break
        if not has_id and heading:
            chain.append({"title": heading, "level": level, "tags": tags})
        cur = getattr(cur, "parent", None)
    chain.reverse()
    return chain


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
    # shallow_tags, NOT tags: `tags` already includes everything inherited
    # from ancestors. Storing the inherited set and then RENDERING it
    # explicitly on the heading makes the child inherit its parent's tags a
    # second time on reparse -- a superset that grows every round trip.
    # Observed 2026-08-10 once the tree was rebuilt: a firm task carrying
    # (firm, plur, research) came back as (firm, infra, plur, research,
    # security). Store what the task itself declares; let org-mode do
    # inheritance, which is its job.
    own = getattr(node, "shallow_tags", None)
    if own is None:
        own = node.tags or []
    tags = sorted(t for t in own if t)
    return {
        "id": node.get_property("ID"),
        "title": node.heading,
        "state": node.todo,
        "space": space,
        "scheduled": str(node.scheduled) if node.scheduled else None,
        "deadline": str(node.deadline) if node.deadline else None,
        "tags": tags,
        #: The task's EFFECTIVE tags in the source file, inheritance included.
        #: Normally redundant -- the projection rebuilds the tree, so org
        #: re-derives them. But a task whose parent is DONE has no parent in
        #: the projection, so it silently loses whatever it inherited from it.
        #: Kept so a promoted orphan can carry its tags explicitly instead.
        "effective_tags": sorted(t for t in (node.tags or []) if t),
        "org": {
            "priority": node.priority,
            "body": node.body or "",
            "properties": {
                k: v for k, v in (node.properties or {}).items()
                if k not in ("ID", "CREATED")
            },
        },
        #: Nearest ID-bearing ancestor, or None for a top-level task. Without
        #: it the projection is flat and 640 nested tasks in this installation
        #: would surface as siblings of their own parents.
        "parent": _parent_id(node),
        #: Heading depth as it stood in the source file. `parent` alone cannot
        #: reconstruct it: a task under a plain (ID-less) section heading has
        #: no task parent yet is not top-level, and rendering it at depth 1
        #: would silently promote it out of its section.
        "level": getattr(node, "level", None),
        #: Non-task ancestor headings, outermost first. Without these the
        #: projection re-parents tasks under whatever heading precedes them.
        "outline": _outline(node),
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
    sections: dict[str, dict] = {}

    ws = OrgWorkspace()
    ws.load(str(org_file))

    # File-level tags apply to every heading in the file. They are not any
    # one task's property, so no task payload can carry them alone -- but a
    # projection that omits the declaration silently strips a tag from every
    # task. Same class as the missing #+SEQ_TODO: file metadata is data.
    filetags: list[str] = []
    for line in org_file.read_text().split("\n")[:40]:
        if line.upper().startswith("#+FILETAGS:"):
            filetags = sorted(t for t in line.split(":", 1)[1].split(":") if t.strip())
            break
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
        payload = task_payload(node, space, date, rung)
        payload["filetags"] = filetags
        result.importable.append(payload)
        # Walk up and record any state-less ancestor headings this task needs
        # in order to sit where it did. Collected in a dict so an ancestor
        # shared by fifty tasks is imported once.
        anc = getattr(node, "parent", None)
        while anc is not None:
            try:
                aid = anc.get_property("ID")
                a_state = anc.todo
            except Exception:  # noqa: BLE001 — root/sentinel
                break
            if aid and not a_state and aid not in known and aid not in sections:
                sections[aid] = _section_payload(anc, space)
            anc = getattr(anc, "parent", None)

    # Sections first: a parent must exist before the children that name it.
    result.importable = list(sections.values()) + result.importable
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
