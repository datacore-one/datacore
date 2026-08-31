"""Phase 0 shadow projection (DIP-0043): run the migration without risking it.

The projector writes the projection under `.datacore/state/projections/` and
never touches the real one. A diff between them is the migration's own test,
run against production data every day, with nothing at stake if it is wrong.

The flip to Phase 1 is gated on N consecutive days of an empty diff -- the
DIP-0035 job-contract pattern applied to a migration. That gate is why this
can run on real spaces today: the worst case is a stale extra file.

`compare()` diffs by TASK, not by bytes. A byte diff would report the header,
the ordering and the drawer layout on every run and drown the one thing that
matters: whether a task, its state, its tags or its dates changed crossing the
boundary. Byte-equality is a Phase 1 concern, once the real file IS the
projection.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from .fold import fold
from .log import read_events
from .projector import project


@dataclass
class ShadowDiff:
    """Task-level differences between the real org file and the projection."""

    space: str
    only_in_org: list[str] = field(default_factory=list)
    only_in_projection: list[str] = field(default_factory=list)
    changed: dict[str, tuple] = field(default_factory=dict)
    org_count: int = 0
    projection_count: int = 0

    @property
    def clean(self) -> bool:
        return not (self.only_in_org or self.only_in_projection or self.changed)

    def __str__(self) -> str:
        status = "CLEAN" if self.clean else (
            f"lost={len(self.only_in_org)} extra={len(self.only_in_projection)} "
            f"changed={len(self.changed)}"
        )
        return (f"{self.space:14} org={self.org_count:4} "
                f"projected={self.projection_count:4}  {status}")


def _tasks(path: Path, states: tuple[str, ...]) -> dict[str, tuple]:
    """Task fingerprints by id: what must survive the boundary unchanged."""
    lib = Path(__file__).resolve().parents[1]
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from org_workspace import OrgWorkspace

    ws = OrgWorkspace()
    ws.load(str(path))
    out: dict[str, tuple] = {}
    for node in ws.all_nodes():
        if node.todo not in states:
            continue
        node_id = node.get_property("ID")
        if not node_id:
            continue
        out[node_id] = (
            node.todo,
            node.heading,
            tuple(sorted(node.tags or [])),
            str(node.scheduled or ""),
            str(node.deadline or ""),
        )
    return out


def compare(space_dir: Path, org_file: Path | None = None) -> ShadowDiff:
    """Diff the real org file against what the ledger would render."""
    from .genesis import ACTIVE_STATES

    org_file = org_file or (space_dir / "org" / "next_actions.org")
    diff = ShadowDiff(space=space_dir.name)
    if not org_file.exists():
        return diff

    # OUTSIDE org/, and this is the root cause of the ID churn.
    #
    # The projection reproduces every :ID: by design — that is what makes it a
    # projection. Written as `org/next_actions.projected.org` it sat beside the
    # authored file, so ANY tool that loads more than one org file from that
    # directory saw every id twice. Measured: loading both into one workspace
    # produces 605 duplicate-ID warnings, and `dedup_ids()` regenerates the
    # duplicates on load. A later save persists that, autosave commits it, and
    # 1,204 ids change.
    #
    # It was also TRACKED IN GIT in all nine spaces, so the condition was
    # committed, pushed and pulled to every machine — which is why the churn
    # kept recurring on winston and returned through Gitea after the outage.
    #
    # Derived content is regenerated, never tracked (DIP-0046 payload classes).
    # `.datacore/state/projections/` is scanned by no org globber.
    proj_dir = space_dir / ".datacore" / "state" / "projections"
    proj_dir.mkdir(parents=True, exist_ok=True)
    projected_path = proj_dir / (org_file.stem + ".projected.org")
    projection = project(fold(read_events(space_dir)), space=space_dir.name)
    projected_path.write_text(projection.text, encoding="utf-8")

    real = _tasks(org_file, ACTIVE_STATES)
    shadow = _tasks(projected_path, ACTIVE_STATES)

    # INBOX ITEMS ARE NOT NEXT_ACTIONS DRIFT.
    #
    # ledger_ingest_org.py ingests from ("inbox.org", "next_actions.org"), so
    # both files' tasks reach the ledger and therefore the projection. This
    # diff is against next_actions.org, so every task captured in inbox.org
    # counted as drift the moment it was ingested — permanently, through no
    # fault of the system.
    #
    # Measured 2026-08-13: 25 items reported as extra across four spaces; a
    # fleet-wide scan found exactly THREE genuinely orphaned. The other 22 were
    # ordinary inbox captures, counted as corruption by the gate that decides
    # whether Phase 1 may proceed — which made the gate unreachable rather than
    # merely slow.
    #
    # Phase 1 replaces next_actions.org and nothing else, so what this gate
    # must measure is next_actions fidelity. An item that currently lives in
    # inbox.org belongs to capture, not to the action list.
    #
    # Rejected alternative: diffing against BOTH files. Tried and measured —
    # it made things worse (5/9 clean -> 2/9, 336 phantom "lost" in
    # 0-personal), because the ledger holds only the inbox items that were
    # ingested, not every capture line. Comparing against the whole inbox is no
    # more apples-to-apples than ignoring it.
    if org_file.name == "next_actions.org":
        # AN ITEM AUTHORED IN ANOTHER ORG FILE IS NOT NEXT_ACTIONS DRIFT.
        #
        # Generalised from the inbox.org-only exclusion, for the same reason
        # and by the same argument. Phase 1 replaces next_actions.org and
        # nothing else, so this gate measures next_actions fidelity; an item
        # that lives in some other authored file is not missing, it is
        # elsewhere. The ledger holds it because something legitimately put it
        # there -- ledger_ingest_org reads inbox.org too, and nightshift emits
        # events for the tasks it runs, which live in nightshift.org.
        #
        # Measured: 21 of 0-personal's 34 remaining "extra" were ordinary
        # nightshift.org tasks, counted as corruption by the gate that decides
        # whether Phase 1 may proceed.
        #
        # EVERY id in those files, not just the ones in an active state. A
        # capture line frequently carries no TODO keyword at all — that is what
        # capture IS — so filtering by state let most inbox items straight back
        # through and the exclusion silently did almost nothing.
        #
        # Still an exclusion from the SHADOW side only. Adding those files to
        # the real side was tried and measured worse (5/9 clean -> 2/9, 336
        # phantom "lost" in 0-personal): the ledger holds only what was
        # ingested, not every authored line, so comparing against a whole
        # second file is no more apples-to-apples than ignoring it.
        import re as _re
        elsewhere: set[str] = set()
        for other in sorted((space_dir / "org").glob("*.org")):
            if other.name == "next_actions.org" or "archive" in other.name.lower():
                continue
            try:
                elsewhere |= set(_re.findall(r":ID:\s*(\S+)",
                                             other.read_text(errors="replace")))
            except OSError:
                continue
        shadow = {k: v for k, v in shadow.items() if k not in elsewhere}
    diff.org_count, diff.projection_count = len(real), len(shadow)
    diff.only_in_org = sorted(set(real) - set(shadow))
    diff.only_in_projection = sorted(set(shadow) - set(real))
    diff.changed = {
        k: (real[k], shadow[k])
        for k in sorted(set(real) & set(shadow))
        if real[k] != shadow[k]
    }
    return diff
