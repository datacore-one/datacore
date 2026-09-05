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

from .fold import LedgerState, closure_kind

#: The projection MUST declare its own todo keywords. Without this line
#: org-mode's custom states (DEFERRED, QUEUED, WORKING, REVIEW, FAILED) parse
#: only if some other file declaring them happened to be read first -- so the
#: same projection parsed back gave 574 tasks alone and 508 inside a
#: multi-space loop, silently reporting 66 tasks "lost" in the migration gate.
#: A generated file whose meaning depends on what was parsed before it is
#: broken; found 2026-08-10 by running the gate for real across nine spaces.
# DIP-0009 v2.0 canon (2026-08-29): seven states, agents-as-workers. Must
# match the live-file header exactly or generated projections/checkpoints
# diverge from authored files.
SEQ_TODO = (
    "#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) REVIEW(r!) "
    "| DONE(d!) DEFERRED(f!) CANCELLED(c!)"
)

GENERATED_HEADER = (
    "# -*- GENERATED FILE — DO NOT EDIT -*-\n"
    "# Rendered from the event ledger (DIP-0043). Edits here are not the\n"
    "# source of truth and will be refused or overwritten. To change a task,\n"
    "# append an event; this file is a view.\n"
)

#: Statuses that still represent live work.
LIVE_STATUSES = ("created", "claimed", "granted")

#: Statuses that mean the work is finished.
CLOSED_STATUSES = ("completed", "verified", "dismissed")

#: How long finished work stays visible in the projection, as DONE, before it
#: is archived.
#:
#: These used to be dropped outright, which made "completed" and "never
#: existed" render identically -- a task you finished simply disappeared from
#: the file, with nothing to show for it. That is the wrong trade for a GTD
#: system: seeing what you finished is half of why the list exists, and a
#: weekly DONE report has nothing to read if the only record is an event log.
#:
#: One day, so the list still opens clean tomorrow morning while today's work
#: is visible in the place you actually look.
CLOSED_RETENTION_DAYS = 1


def _closed_within(item, days: int) -> bool:
    """Was this item closed inside the retention window?

    `closed_at` is an HLC -- "<ms-epoch>.<counter>.<actor>" -- so the timestamp
    is the leading field. An item closed before the fold began recording the
    moment has no value here; treat it as OLD rather than recent, so a
    retro-fitted field cannot resurrect a year of finished work into the
    projection on first run.
    """
    raw = getattr(item, "closed_at", None)
    if not raw:
        return False
    try:
        import time
        ms = float(str(raw).split(".")[0])
        return (time.time() - ms / 1000.0) <= days * 86400
    except (ValueError, TypeError):
        return False


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


_STAMP_DAY = re.compile(r"([<\[])(\d{4})-(\d{2})-(\d{2}) [A-Za-z]{2,3}(?=[ >\]])")


def _fix_day(m: "re.Match") -> str:
    import datetime as _dt
    try:
        day = _dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4))).strftime("%a")
    except ValueError:
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}-{m.group(3)}-{m.group(4)} {day}"


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
    # A bracketed stamp is passed through -- except its DAY NAME, which is
    # recomputed from the date. Typed weekdays reach the ledger from org files
    # (`<2026-07-30 Wed>`; it was a Thursday) and a projection that repeats
    # them fails the date hook on every autosave (4-forge, 2026-09-04).
    text = _STAMP_DAY.sub(_fix_day, text)
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



_TRAILING_TAG_BLOCK = re.compile(r"\s+:([^\s:]+(?::[^\s:]+)*):\s*$")
# The alphabet is the PARSER'S, not org's. Org itself also admits `#` and `%`
# in a tag, but the importer this projection must round-trip through (the
# vendored orgparse behind org_workspace) does not: given
# `:AI:enterprise#373:infra:pm:` it kept `infra`/`pm` and pushed
# ` :AI:enterprise#373` back INTO the title. 5-plur's org-7aba0a999bc5 failed
# the checkpoint restore on exactly that from 2026-08-31 -- a tag the renderer
# considered valid and the parser could not read. What is written here has to
# come back through that parser unchanged, so its alphabet is the constraint.
_BAD_TAG_CHAR = re.compile(r"[^A-Za-z0-9_@]")


def _clean_title_and_tags(title: str, tags) -> tuple[str, list[str]]:
    """Return (title without an embedded tag block, valid sorted org tags).

    Only [A-Za-z0-9_@] survive in a tag; one hyphen voids every tag on the
    heading. A heading ingested with `:docs:wrap-up-extracted:` had the
    whole block left INSIDE its title (the parser saw no valid tags), and this
    renderer wrote it back verbatim -- so the checkpoint failed the org-tag
    hook and winston's 5-plur autosave was refused every 15 minutes from
    2026-09-03. The projection must always be valid org, whatever the ledger
    holds: embedded blocks are split out, invalid characters become `_`.
    """
    title = title or ""
    found: list[str] = []
    m = _TRAILING_TAG_BLOCK.search(title)
    if m:
        found = m.group(1).split(":")
        title = title[: m.start()].rstrip()
    clean = {_BAD_TAG_CHAR.sub("_", t) for t in list(tags or []) + found if t}
    return title, sorted(clean)

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
        title, tags = _clean_title_and_tags(item.title, payload.get("tags"))
        tag_str = f"  :{':'.join(tags)}:" if tags else ""
        out = [f"{stars} {title}{tag_str}"]
        out.extend("  " + ln for ln in _drawer({"ID": item.id}))
        return out
    # A CLOSED ITEM RENDERS AS DONE, WHATEVER IT WAS BEFORE.
    #
    # The payload keeps the state the task had while it was live (TODO, NEXT,
    # WAITING). Rendering that after completion would show finished work as
    # still outstanding -- worse than dropping it, because it reads as a lie
    # rather than an omission. `dismissed` renders as CANCELLED: giving up on
    # something and finishing it are different outcomes and a weekly report
    # that conflates them is not worth reading.
    if item.status in CLOSED_STATUSES:
        from .fold import was_finished
        state = "DONE" if was_finished(item) else "CANCELLED"
    else:
        state = payload.get("state") or "TODO"
    priority = org.get("priority")
    prio = f"[#{priority}] " if priority else ""
    # sorted() again, not redundantly: a payload can reach here from any
    # producer, and determinism must not depend on every producer remembering.
    title, tags = _clean_title_and_tags(item.title, payload.get("tags"))
    tag_str = f"  :{':'.join(tags)}:" if tags else ""
    lines = [f"{stars} {state} {prio}{title}{tag_str}"]

    if item.status in CLOSED_STATUSES and getattr(item, "closed_at", None):
        # An org CLOSED: stamp, so a weekly report can find finished work by
        # date without re-folding the whole event log.
        try:
            import datetime
            ms = float(str(item.closed_at).split(".")[0])
            when = datetime.datetime.fromtimestamp(ms / 1000.0)
            lines.append("  CLOSED: " + when.strftime("[%Y-%m-%d %a %H:%M]"))
        except (ValueError, TypeError):
            pass

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
    # Body text is copied verbatim, so a typed weekday inside it (a DEADLINE
    # line captured as body, 4-forge 2026-09-04) came back on every projection.
    body = _STAMP_DAY.sub(_fix_day, body)
    if body:
        lines.extend(body.split("\n"))
    return lines


def projected_items(state: LedgerState, *, space: str | None = None) -> list:
    """The items a projection of `state` contains -- the ONE definition.

    Live work, plus work finished recently enough to still be worth seeing;
    anything closed longer ago belongs to the archive, not the action list.
    Exposed because the checkpoint verifier must know exactly which ancestors
    are rendered: a child inherits tags from a parent that is in the file and
    from nothing else, and deciding that with a second copy of this filter is
    how the two drift apart.
    """
    # A housekeeping closure is not finished work: a twin dismissed after an
    # id regeneration, or an orphan reconciled away, was never done or dropped
    # by anyone. Rendering it for the retention day put the same :ID: in the
    # file twice (2-datacore, 2026-09-05: two subtrees under a CANCELLED twin
    # and its live successor), which the churn detector counted as duplicates
    # and org-workspace's dedup would have "repaired" by minting fresh ids.
    return [
        item for item in state.items.values()
        if (item.status in LIVE_STATUSES
            or (item.status in CLOSED_STATUSES
                and _closed_within(item, CLOSED_RETENTION_DAYS)
                and closure_kind(item) != "housekeeping"))
        and (space is None or (item.payload or {}).get("space", space) == space)
    ]


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
    items = projected_items(state, space=space)

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
