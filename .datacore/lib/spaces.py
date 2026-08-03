"""Space discovery.

A directory is a space if it carries ``.datacore/config.yaml`` with a ``space:``
key — the way git finds ``.git`` and monorepo tools find ``package.json``.
Location stops mattering, so a space may live at the install root, nested under
another space, or anywhere else.

This replaces globbing ``[0-9]-*/`` at the install root, which was duplicated
across fifteen call sites and conflated three separate things: whether a
directory *is* a space, what *kind* of space it is, and its local sort order.
The numeric prefix is a per-install ordinal — the same space repo is ``5-x`` in
one install and ``9-x`` in another — so it never carried identity.

Migration is deliberately non-breaking. ``discover_spaces()`` returns the
**union** of marker-discovered and glob-discovered directories, so nothing that
was found before stops being found. ``discovery_discrepancy()`` reports where
the two disagree; once it is empty for every install, ``include_legacy`` can
default to False and the glob can go. See DIP-0015 and issue #41.

Typical use::

    from spaces import discover_spaces

    for space in discover_spaces():
        next_actions = space.path / "org" / "next_actions.org"

    for space in discover_spaces(types={"team"}):
        ...
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

MARKER = Path(".datacore") / "config.yaml"

#: Legacy pattern. Retained so a space that has not yet gained a marker keeps
#: being discovered. Remove once discovery_discrepancy() is empty everywhere.
LEGACY_GLOB = "[0-9]-*"

#: A client space nested under its owner sits at e.g.
#: ``<root>/<space>/1-tracks/clients/<name>`` — depth 4 — so 4 is the shallowest
#: bound that works today and leaves no headroom. One extra grouping directory
#: would push a space out of discovery *silently*, which is the failure mode
#: this module exists to remove. 5 costs one more level of a walk that already
#: skips everything expensive.
MAX_DEPTH = 5

#: Never descended into. ``2-projects`` holds cloned repositories with their own
#: dependency trees and is the single biggest cost in an unbounded walk.
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "2-projects", "4-archive", ".obsidian",
    "dist", "build", ".next", "target",
})


@dataclass(frozen=True)
class Space:
    """One discovered space."""

    path: Path
    name: str
    type: str
    owner: str | None = None
    marked: bool = True
    """False when found only by the legacy glob — it has no marker yet."""

    @property
    def ordinal(self) -> int | None:
        """The local sort prefix, if the directory has one.

        Purely cosmetic and per-install. Never use it as identity.
        """
        head = self.path.name.split("-", 1)[0]
        return int(head) if head.isdigit() else None


def data_root() -> Path:
    """The install root. ``DATACORE_ROOT`` wins, else ``~/Data``."""
    return Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))


def read_marker(path: Path) -> dict | None:
    """The ``space:`` block from ``path``'s marker, or None if it is not a space.

    A malformed or unreadable marker is not a space, and says so in the log
    rather than raising — one bad file must not take out discovery for every
    other space.
    """
    marker = path / MARKER
    if not marker.is_file():
        return None
    try:
        loaded = yaml.safe_load(marker.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        log.warning("space marker unreadable, skipping: %s (%s)", marker, exc)
        return None
    block = loaded.get("space")
    if not isinstance(block, dict):
        return None
    return block


def _walk(root: Path, depth: int = 1):
    """Directories worth testing for a marker, breadth-first, depth-bounded."""
    if depth > MAX_DEPTH:
        return
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except (PermissionError, OSError):
        return
    for entry in entries:
        if entry.name in SKIP_DIRS or entry.is_symlink():
            continue
        yield entry
        yield from _walk(entry, depth + 1)


def _legacy_dirs(root: Path) -> list[Path]:
    """Directories the old ``[0-9]-*/`` glob would have matched."""
    return sorted(p for p in root.glob(LEGACY_GLOB) if p.is_dir())


def _from_marker(path: Path, block: dict) -> Space:
    return Space(
        path=path,
        name=str(block.get("name") or _implied_name(path)),
        type=str(block.get("type") or "unknown"),
        owner=block.get("owner"),
        marked=True,
    )


def _implied_name(path: Path) -> str:
    """Directory name with any local ordinal prefix stripped."""
    head, _, tail = path.name.partition("-")
    return tail if head.isdigit() and tail else path.name


def discover_spaces(
    root: Path | None = None,
    *,
    types: set[str] | None = None,
    include_legacy: bool = True,
) -> list[Space]:
    """Every space under ``root``, marker-discovered plus (by default) legacy.

    Args:
        root: install root. Defaults to :func:`data_root`.
        types: keep only these ``space.type`` values. Legacy directories have
            no declared type, so a ``types`` filter necessarily excludes them.
        include_legacy: also return ``[0-9]-*/`` directories that carry no
            marker. True during migration so nothing silently disappears.

    Returns:
        Spaces sorted by path. Marker-discovered entries win over legacy ones
        for the same directory.
    """
    root = root or data_root()
    found: dict[Path, Space] = {}

    for candidate in _walk(root):
        block = read_marker(candidate)
        if block is not None:
            found[candidate] = _from_marker(candidate, block)

    if include_legacy:
        for path in _legacy_dirs(root):
            if path in found:
                continue
            found[path] = Space(
                path=path,
                name=_implied_name(path),
                type="unknown",
                owner=None,
                marked=False,
            )

    spaces = sorted(found.values(), key=lambda s: s.path)
    if types is not None:
        spaces = [s for s in spaces if s.type in types]
    return spaces


def discovery_discrepancy(root: Path | None = None) -> tuple[set[Path], set[Path]]:
    """Where marker and glob discovery disagree.

    Returns ``(marker_only, legacy_only)``. ``legacy_only`` is the set that
    would vanish if the glob were dropped today — it must be empty before
    ``include_legacy`` is turned off. ``marker_only`` is the set the glob never
    saw, which is the point of the change.
    """
    root = root or data_root()
    marker = {s.path for s in discover_spaces(root, include_legacy=False)}
    legacy = set(_legacy_dirs(root))
    return marker - legacy, legacy - marker


def find_space(path: Path, root: Path | None = None) -> Space | None:
    """The innermost space containing ``path``, or None.

    Innermost matters once spaces nest: a path inside a client space owned by a
    team space belongs to the client space, not the team one.
    """
    root = root or data_root()
    path = path.resolve()
    best: Space | None = None
    for space in discover_spaces(root):
        try:
            path.relative_to(space.path.resolve())
        except ValueError:
            continue
        if best is None or len(space.path.parts) > len(best.path.parts):
            best = space
    return best
