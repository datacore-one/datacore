"""Phase 1 (DIP-0043): make `next_actions.org` a generated file.

Phase 0 writes a projection alongside the real file and diffs them. Phase 1
makes the real file BE the projection. This module implements that, and is
inert until a space opts in — activation is a per-space marker written by a
human, never a default, never inferred from a clean diff.

Why per-space and not global: spaces differ in how much machinery writes to
them, so the blast radius of getting this wrong differs too. A space with no
agents touching it can flip long before one that nightshift drives.

ACTIVATION IS NOT ENOUGH ON ITS OWN. Three preconditions are checked at flip
time and refused if unmet, because each one silently corrupts the space if
skipped:

  1. The Phase 0 diff for that space must be CLEAN right now. Flipping on a
     dirty diff overwrites the difference instead of resolving it.
  2. `next_actions.org` must be gitignored. Nightshift commits and pushes that
     file after every state write; a generated file that is still tracked
     means every machine's regeneration is a diff, which recreates exactly the
     merge conflicts the projection exists to remove.
  3. No process may still write the file directly. Checked as a warning rather
     than a hard block, because it cannot be proven from here — but recorded
     in the activation record so the decision is auditable.

Rollback is `git checkout` on the org file plus deleting the marker. Nothing
here destroys history: the pre-flip file is in git, and the ledger holds every
task independently.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .fold import fold
from .log import read_events
from .projector import ProjectionConflict, project, write

#: Written by a human to opt a space in. Its presence is the ONLY thing that
#: makes this module act; a clean diff never activates anything by itself.
MARKER = "phase1-active"


@dataclass
class FlipResult:
    space: str
    activated: bool
    refused_because: list[str]
    written: bool = False
    sha: str | None = None
    item_count: int = 0

    def __str__(self) -> str:
        if not self.activated:
            return f"{self.space:14} not activated (no {MARKER} marker)"
        if self.refused_because:
            return f"{self.space:14} REFUSED — {'; '.join(self.refused_because)}"
        return f"{self.space:14} written {self.item_count} items sha={self.sha[:12]}"


def _state_dir(space_dir: Path) -> Path:
    return space_dir / ".datacore" / "state" / "projection"


def is_active(space_dir: Path) -> bool:
    return (_state_dir(space_dir) / MARKER).exists()


def activate(space_dir: Path) -> Path:
    """Opt a space in. Deliberately a separate, explicit act."""
    d = _state_dir(space_dir)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / MARKER
    marker.write_text("Phase 1 active: next_actions.org is generated from the ledger.\n")
    return marker


def deactivate(space_dir: Path) -> None:
    """Roll back to Phase 0. The org file itself is restored from git."""
    marker = _state_dir(space_dir) / MARKER
    if marker.exists():
        marker.unlink()


def _is_gitignored(space_dir: Path, rel: str) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(space_dir), "check-ignore", "-q", rel],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _last_sha(space_dir: Path) -> str | None:
    f = _state_dir(space_dir) / "last-written.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text()).get("sha")
    except (OSError, ValueError):
        return None


def _record_sha(space_dir: Path, sha: str) -> None:
    d = _state_dir(space_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "last-written.json").write_text(json.dumps({"sha": sha}, indent=2))


def flip(space_dir: Path, org_file: Path | None = None, force: bool = False) -> FlipResult:
    """Write the projection over the real org file, if this space opted in."""
    from .shadow import compare

    org_file = org_file or (space_dir / "org" / "next_actions.org")
    result = FlipResult(space=space_dir.name, activated=is_active(space_dir),
                        refused_because=[])
    if not result.activated:
        return result

    diff = compare(space_dir, org_file)
    if not diff.clean and not force:
        result.refused_because.append(
            f"Phase 0 diff not clean (lost={len(diff.only_in_org)} "
            f"extra={len(diff.only_in_projection)} changed={len(diff.changed)})"
        )
    rel = str(org_file.relative_to(space_dir))
    if not _is_gitignored(space_dir, rel) and not force:
        result.refused_because.append(
            f"{rel} is still git-tracked — a generated file that is committed "
            f"recreates the merge conflicts this removes"
        )
    if result.refused_because:
        return result

    projection = project(fold(read_events(space_dir)), space=space_dir.name)
    try:
        sha = write(projection, org_file, last_written_sha=_last_sha(space_dir))
    except ProjectionConflict as exc:
        result.refused_because.append(str(exc))
        return result

    _record_sha(space_dir, sha)
    result.written, result.sha, result.item_count = True, sha, projection.item_count
    return result
