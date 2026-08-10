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

import hashlib
from dataclasses import dataclass
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


def render_item(item, *, level: int = 2) -> list[str]:
    """One ledger item as org lines. Pure; no I/O, no clock."""
    payload = item.payload or {}
    org = payload.get("org") or {}

    stars = "*" * level
    state = payload.get("state") or "TODO"
    priority = org.get("priority")
    prio = f"[#{priority}] " if priority else ""
    # sorted() again, not redundantly: a payload can reach here from any
    # producer, and determinism must not depend on every producer remembering.
    tags = sorted(payload.get("tags") or [])
    tag_str = f"  :{':'.join(tags)}:" if tags else ""
    lines = [f"{stars} {state} {prio}{item.title}{tag_str}"]

    sched, dead = payload.get("scheduled"), payload.get("deadline")
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
    items = [
        item for item in state.items.values()
        if item.status in LIVE_STATUSES
        and (space is None or (item.payload or {}).get("space") == space)
    ]
    items.sort(key=lambda i: i.id)

    lines = [GENERATED_HEADER.rstrip("\n"), "", SEQ_TODO, ""]
    for item in items:
        lines.extend(render_item(item))
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
