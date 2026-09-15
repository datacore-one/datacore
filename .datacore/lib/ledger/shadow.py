"""Compare all authored content with the candidate projection before migration.

The shadow stays outside org/ so it cannot introduce duplicate source IDs.
A clean result covers bodies, properties, structure and file preamble as well
as task labels. Ambiguous input is a failed check, never an empty task set.
"""

from __future__ import annotations

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
    problems: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.only_in_org or self.only_in_projection or self.changed or self.problems)

    def __str__(self) -> str:
        status = "CLEAN" if self.clean else (
            f"lost={len(self.only_in_org)} extra={len(self.only_in_projection)} "
            f"changed={len(self.changed)} invalid={len(self.problems)}"
        )
        return (f"{self.space:14} org={self.org_count:4} "
                f"projected={self.projection_count:4}  {status}")



def compare(space_dir: Path, org_file: Path | None = None) -> ShadowDiff:
    """Check the same full representation the projection guard protects."""
    from org_transaction import serialized
    return serialized(_compare)(space_dir, org_file)


def _compare(space_dir: Path, org_file: Path | None = None) -> ShadowDiff:
    from org_transaction import watch_file, write_org_text
    from org_workspace._vendor.orgparse import loads
    from ledger_project_org import _with_org_header
    from .projection_state import snapshot, ProjectionConflict

    org_file = org_file or (space_dir / 'org/next_actions.org')
    diff = ShadowDiff(space=space_dir.name)
    source = watch_file(org_file)['before']
    if source is None:
        diff.problems.append('source file is missing')
        return diff
    try:
        candidate = project(fold(read_events(space_dir)), space=space_dir.name).text
        candidate = _with_org_header(space_dir, org_file, candidate, remember=False)
        projected_path = space_dir / '.datacore/state/projections' / (org_file.stem + '.projected.org')
        write_org_text(projected_path, candidate)
        real_doc, shadow_doc = snapshot(source, space_dir.name), snapshot(candidate, space_dir.name)
        real, shadow = real_doc['items'], shadow_doc['items']
        if real_doc['preamble'] != shadow_doc['preamble']:
            diff.changed['file preamble'] = (real_doc['preamble'], shadow_doc['preamble'])
        if org_file.name == 'next_actions.org':
            # A capture confined to another file is not an extra action.
            # An ID present in this source remains checked even if a twin
            # also appears elsewhere: that twin cannot excuse lost content.
            elsewhere = set()
            for other in sorted((space_dir / 'org').glob('*.org')):
                if other == org_file or 'archive' in other.name.lower():
                    continue
                text = watch_file(other)['before']
                if text is not None:
                    elsewhere.update(n.get_property('ID') for n in loads(text)[1:] if n.get_property('ID'))
            shadow = {key: value for key, value in shadow.items() if key in real or key not in elsewhere}
        diff.org_count, diff.projection_count = len(real), len(shadow)
        diff.only_in_org = sorted(set(real) - set(shadow))
        diff.only_in_projection = sorted(set(shadow) - set(real))
        diff.changed.update({key: (real[key], shadow[key]) for key in sorted(set(real) & set(shadow))
                             if real[key] != shadow[key]})
    except (ProjectionConflict, OSError, UnicodeError) as exc:
        diff.problems.append(str(exc))
    return diff
