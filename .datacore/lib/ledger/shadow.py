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
    diff.org_count, diff.projection_count = len(real), len(shadow)
    diff.only_in_org = sorted(set(real) - set(shadow))
    diff.only_in_projection = sorted(set(shadow) - set(real))
    diff.changed = {
        k: (real[k], shadow[k])
        for k in sorted(set(real) & set(shadow))
        if real[k] != shadow[k]
    }
    return diff
