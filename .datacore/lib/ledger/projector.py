"""Org projection (DIP-0043): render folded ledger state back to org-mode.

The direction that makes the ledger authoritative. `genesis.py` brings org
INTO the ledger; this renders the ledger back OUT, so an org file can become a
generated artifact instead of multi-writer mutable state.

Three properties, each of which exists because of a specific way this can go
wrong:

DETERMINISTIC. Same `LedgerState` in, byte-identical text out, every time, on
every machine. Nothing here reads the clock, the hostname, an absolute path,
or a set/dict in nondeterministic order. Without this, two machines rendering
the same state produce different bytes and every drift check is a false alarm
-- which trains an operator to ignore the alert that matters.

REFUSE TO OVERWRITE. `write()` will not clobber a file whose content changed
since this projector last wrote it. It raises `ProjectionConflict` instead.
This is the single guard that turns "the projector silently ate the edit you
made on your phone" into "the system told you". It is why Phase 1 is safe to
turn on before the Phase 2 reconciler exists.

ORIGINAL PATH. The projected file keeps its exact name (`next_actions.org`),
so org-workspace, the GTD MCP tools, agenda queries and mobile clients keep
resolving it. Only its provenance changes -- announced by a header.
"""

from __future__ import annotations

import re
from datetime import date

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

from .fold import LedgerState

#: The projection MUST declare its own todo keywords. Without this line
#: org-mode's custom states (DEFERRED, QUEUED, WORKING, REVIEW, FAILED) parse
#: only if some other file declaring them happened to be read first -- so the
#: same projection parsed back gave 574 tasks alone and 508 inside a
#: multi-space loop, silently reporting 66 tasks "lost" in the migration gate.
#: A generated file whose meaning depends on what was parsed before it is
#: broken; found 2026-08-10 by running the gate for real across nine spaces.
SEQ_TODO = (
    "#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) DEFERRED(f) QUEUED(q) "
    "WORKING(W!) REVIEW(r!) | DONE(d!) FAILED(x!) CANCELLED(c!)"
)

GENERATED_HEADER = (
    "# -*- GENERATED FILE — DO NOT EDIT -*-\n"
    "# Rendered from the event ledger (DIP-0043). Edits here are not the\n"
    "# source of truth and will be refused or overwritten. To change a task,\n"
    "# append an event; this file is a view.\n"
)

#: Statuses that still represent live work. `completed`/`verified`/`dismissed`
#: items leave the projection the same way a DONE task leaves next_actions.org.
LIVE_STATUSES = ("created", "claimed", "granted")


class ProjectionConflict(RuntimeError):
    """The target file changed since this projector last wrote it.

    Carries both hashes so the caller can show what it is refusing to do,
    rather than just declining.
    """

    def __init__(self, path: Path, expected: str, found: str) -> None:
        super().__init__(
            f"{path} changed since the projector last wrote it "
            f"(expected sha256 {expected[:12]}, found {found[:12]}) — "
            f"refusing to overwrite; reconcile the edit into events first"
        )
        self.path, self.expected, self.found = path, expected, found


@dataclass(frozen=True)
class Projection:
    text: str
    item_count: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()



_BARE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _org_stamp(value):
    """Render a date as a VALID org timestamp: `<YYYY-MM-DD Day>`.

    Some writers store `scheduled` as a bare `2026-08-14`, and this emitted it
    verbatim. A bare date is not an org timestamp — org-mode requires angle or
    square brackets — so re-importing the projection parsed it as no schedule
    at all. Eight items in 5-plur lost their dates on a restore round-trip.

    That is a checkpoint bug today and a DATA-LOSS bug at the Phase 1 flip,
    when the projection stops being a shadow copy and BECOMES next_actions.org.
    Every bare-date schedule would have been silently erased from the file the
    user actually works in.

    The day name is COMPUTED here, never guessed: a wrong day in an org
    timestamp is exactly the class of error the date tooling exists to prevent.
    Anything already bracketed is passed through untouched — it is already a
    timestamp, and rewriting it would risk dropping a time-of-day or repeater.
    """
    if not value:
        return value
    text = str(value).strip()
    m = _BARE_DATE.match(text)
    if not m:
        return text
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return text          # not a real date; emit as-is rather than invent one
    return f"<{text} {d.strftime('%a')}>"

def _drawer(props: dict) -> list[str]:
    """A PROPERTIES drawer with keys in sorted order.

    Sorted, not insertion-ordered: dict order would make the output depend on
    the order events happened to arrive, which differs per machine.
    """
    if not props:
        return []
    out = [":PROPERTIES:"]
    for key in sorted(props):
        value = props[key]
        if value is None or value == "":
            continue
        out.append(f":{key}: {value}")
    out.append(":END:")
    return out


def render_item(item, *, level: int | None = None) -> list[str]:
    """One ledger item as org lines. Pure; no I/O, no clock.

    Depth comes from the item's own recorded `level` when it has one, so a
    subtask renders as a subtask. An explicit `level=` overrides it (used by
    tests and by any caller re-parenting a subtree).
    """
    payload = item.payload or {}
    if level is None:
        level = payload.get("level") or 2
    org = payload.get("org") or {}

    stars = "*" * level
    if payload.get("section"):
        # A plain heading: no TODO keyword, no drawer beyond its id. Rendering
        # it as a task would invent work that never existed.
        tags = sorted(payload.get("tags") or [])
        tag_str = f"  :{':'.join(tags)}:" if tags else ""
        out = [f"{stars} {item.title}{tag_str}"]
        out.extend("  " + ln for ln in _drawer({"ID": item.id}))
        return out
    state = payload.get("state") or "TODO"
    priority = org.get("priority")
    prio = f"[#{priority}] " if priority else ""
    # sorted() again, not redundantly: a payload can reach here from any
    # producer, and determinism must not depend on every producer remembering.
    tags = sorted(payload.get("tags") or [])
    tag_str = f"  :{':'.join(tags)}:" if tags else ""
    lines = [f"{stars} {state} {prio}{item.title}{tag_str}"]

    sched, dead = _org_stamp(payload.get("scheduled")), _org_stamp(payload.get("deadline"))
    if sched or dead:
        parts = []
        if sched:
            parts.append(f"SCHEDULED: {sched}")
        if dead:
            parts.append(f"DEADLINE: {dead}")
        lines.append("  " + " ".join(parts))

    props = dict(org.get("properties") or {})
    props["ID"] = item.id
    genesis = payload.get("genesis") or {}
    # Only render CREATED when the date actually came from somewhere. The
    # ladder's last rung is a documented placeholder, and writing it as
    # ":CREATED: [1970-01-01]" would dress a known-unknown up as a fact --
    # the precise thing the ladder exists to avoid. An item whose date was
    # defaulted simply carries no CREATED, which is honest and greppable.
    if genesis.get("date") and genesis.get("rung") != "genesis_fallback":
        props["CREATED"] = f"[{genesis['date']}]"
    lines.extend("  " + line for line in _drawer(props))

    body = (org.get("body") or "").rstrip()
    if body:
        lines.extend(body.split("\n"))
    return lines


def project(state: LedgerState, *, space: str | None = None) -> Projection:
    """Render live items from `state` as org text.

    Items are ordered by id -- a total, stable order that does not depend on
    event arrival, fold internals, or dict iteration. Sorting by anything the
    user can see (title, date) would reorder the file whenever a task was
    renamed, producing diff noise that hides real change.
    """
    # An ABSENT space means "this space", not "no space".
    #
    # This filter was `payload["space"] == space`, so an item whose payload
    # simply lacked the field was silently dropped. Events are read from ONE
    # space's log directory, so the log itself already scopes them — an item
    # sitting in this space's log is in this space by construction.
    #
    # Caught by the F2a reversal drill, and it was a Phase 1 blocker: after the
    # flip the org file is generated, so a task appended straight to the ledger
    # (which is the entire point of Phase 1) rendered as nothing. A valid event,
    # accepted by fold, producing a task nobody could see. Only an explicit
    # FOREIGN space is excluded now.
    items = [
        item for item in state.items.values()
        if item.status in LIVE_STATUSES
        and (space is None or (item.payload or {}).get("space", space) == space)
    ]

    # Depth-first by parent, so a child is emitted directly under its parent
    # rather than wherever its id happens to sort. Sorting the whole set by id
    # alone would scatter subtasks away from their parents and silently
    # reparent them to whatever heading preceded them in the flat order.
    by_parent: dict[str | None, list] = {}
    known = {i.id for i in items}
    for item in items:
        parent = (item.payload or {}).get("parent")
        # A parent that is not itself projected (finished, dismissed, or in
        # another space) would strand its children invisibly; treat them as
        # roots so they are still rendered.
        by_parent.setdefault(parent if parent in known else None, []).append(item)
    for bucket in by_parent.values():
        bucket.sort(key=lambda i: i.id)

    ordered: list = []

    def _walk(parent_id, depth):
        for child in by_parent.get(parent_id, []):
            ordered.append((child, depth))
            _walk(child.id, depth + 1)

    # NOTE: `depth` here is the tree depth. Recorded level is preferred below,
    # but CLAMPED to this + nothing deeper, because org files legitimately skip
    # levels (a level-3 task directly under a level-1 section). Rendering the
    # recorded 3 after a level-2 sibling would nest the task under that sibling
    # and inherit its tags -- three tasks in 5-plur picked up anthropic/outreach
    # exactly this way.

    _walk(None, 1)
    # Depth comes from each item's RECORDED level, not from its position in
    # this walk. The two differ whenever a task sat under a plain section
    # heading: that heading is not a task, so it is not a ledger item, so the
    # walk sees the task as a root and would promote it out of its section.
    # Preserving the recorded level keeps the file's shape; the walk is used
    # only to keep children adjacent to their parents.
    # Depth: recorded level when the parent is still present, otherwise
    # promote to top level. A task whose parent is DONE (and so absent from
    # the projection) would keep its recorded depth and physically nest under
    # whatever unrelated item precedes it -- inheriting that item's tags.
    # Observed 2026-08-10: seven tasks came back carrying ai_policy/blog/
    # writing from a neighbour. Promoting matches what org itself does when
    # you archive a parent: the child becomes top-level.
    resolved = []
    for item, depth in ordered:
        payload = item.payload or {}
        parent = payload.get("parent")
        if parent and parent not in known:
            # Promoted orphan: render its effective tags, so the tags it used
            # to inherit from the absent parent are not silently dropped.
            eff = payload.get("effective_tags")
            if eff:
                payload = dict(payload)
                payload["tags"] = eff
                item = replace(item, payload=payload)
            resolved.append((item, 1))
        else:
            recorded = payload.get("level") or depth
            # never deeper than its true position in the tree
            resolved.append((item, min(recorded, depth)))
    items = resolved

    lines = [GENERATED_HEADER.rstrip("\n"), "", SEQ_TODO]
    filetags: list[str] = []
    for item in items:
        ft = (item.payload or {}).get("filetags") if not isinstance(item, tuple) else None
        if ft:
            filetags = ft
            break
    if not filetags:
        for cand in state.items.values():
            ft = (cand.payload or {}).get("filetags")
            if ft:
                filetags = ft
                break
    if filetags:
        lines.append("#+FILETAGS: :" + ":".join(filetags) + ":")
    lines.append("")
    for item, depth in items:
        lines.extend(render_item(item, level=depth))
        lines.append("")
    return Projection(text="\n".join(lines).rstrip("\n") + "\n", item_count=len(items))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(projection: Projection, path: Path, *, last_written_sha: str | None = None,
          force: bool = False) -> str:
    """Write `projection` to `path`, refusing to clobber a changed file.

    `last_written_sha` is what this projector wrote last time. When the file on
    disk no longer hashes to that, someone edited it and the edit is not yet in
    the ledger -- raise rather than destroy it. `force` is for a deliberate
    operator override, never for routine runs.

    Returns the sha256 of what was written, to be passed back as
    `last_written_sha` next time.
    """
    if path.exists() and last_written_sha is not None and not force:
        found = _sha(path)
        if found != last_written_sha:
            raise ProjectionConflict(path, last_written_sha, found)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(projection.text, encoding="utf-8")
    return projection.sha256
