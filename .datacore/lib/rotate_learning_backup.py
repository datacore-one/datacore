#!/usr/bin/env python3
"""
rotate_learning_backup.py — pre-write rotation safety net for .datacore/learning/ files.

Call rotate(filepath) BEFORE any write to a .datacore/learning/ file.
Copies the current file to:
  <root>/.datacore/state/learning-backups/<subdir>/<name>-<YYYYMMDD-HHMMSS>.md
Keeps the last 7 backups per file; purges older ones automatically.
No-op if the source file does not yet exist (first write is not a destructive operation).

Usage (standalone — call from agent shell steps):
  python3 .datacore/lib/rotate_learning_backup.py <filepath>
  python3 .datacore/lib/rotate_learning_backup.py --all       # rotate every learning file

Import:
  from rotate_learning_backup import rotate, rotate_all, rotate_spaces
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
BACKUP_ROOT = DATACORE_ROOT / ".datacore" / "state" / "learning-backups"
KEEP = 7


def _subdir(filepath: Path) -> str:
    """Derive the backup subdirectory from the file's position relative to root.

    .datacore/learning/patterns.md              → _root
    0-personal/.datacore/learning/patterns.md   → 0-personal
    .datacore/modules/trading/.datacore/learning → _modules__trading
    """
    try:
        rel = filepath.resolve().relative_to(DATACORE_ROOT.resolve())
    except ValueError:
        return "_external"

    parts = rel.parts
    for i, part in enumerate(parts):
        if part == ".datacore" and i + 1 < len(parts) and parts[i + 1] == "learning":
            if i == 0:
                return "_root"
            # Join all path segments before .datacore, replacing dots with underscores
            return "__".join(parts[:i]).replace(".", "_")
    return "_other"


def rotate(filepath: Path) -> Path | None:
    """Back up filepath before a write.

    Returns the backup path on success, or None if the source did not exist.
    Raises on I/O errors so callers can detect failures and abort the write.
    """
    filepath = Path(filepath).resolve()
    if not filepath.exists():
        return None

    subdir = _subdir(filepath)
    backup_dir = BACKUP_ROOT / subdir
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = filepath.stem
    suffix = filepath.suffix or ".md"
    backup_path = backup_dir / f"{stem}-{stamp}{suffix}"

    shutil.copy2(str(filepath), str(backup_path))

    # Purge: keep only the KEEP most recent backups for this stem
    pattern = f"{stem}-*{suffix}"
    existing = sorted(backup_dir.glob(pattern))
    for old in existing[:-KEEP]:
        old.unlink(missing_ok=True)

    return backup_path


def _find_learning_files() -> list[Path]:
    """Discover all files under .datacore/learning/ across root, spaces, and modules."""
    files: list[Path] = []

    def _collect(d: Path) -> None:
        if d.is_dir():
            files.extend(f for f in d.iterdir() if f.is_file())

    # Root
    _collect(DATACORE_ROOT / ".datacore" / "learning")

    # Spaces: numeric-prefix directories at root level
    for space_dir in sorted(DATACORE_ROOT.glob("[0-9]-*")):
        if space_dir.is_dir() and not space_dir.name.endswith("-archive"):
            _collect(space_dir / ".datacore" / "learning")

    # Modules
    modules_root = DATACORE_ROOT / ".datacore" / "modules"
    if modules_root.is_dir():
        for module_dir in sorted(modules_root.iterdir()):
            if module_dir.is_dir():
                _collect(module_dir / ".datacore" / "learning")

    return files


def rotate_all() -> list[Path]:
    """Rotate every learning file found. Returns list of backup paths created."""
    backups: list[Path] = []
    for f in _find_learning_files():
        b = rotate(f)
        if b is not None:
            backups.append(b)
    return backups


def rotate_spaces(space_names: list[str]) -> list[Path]:
    """Rotate learning files for the named spaces.

    Accepts space directory names (e.g. '0-personal', '2-datacore') or the
    special token 'root' / '' / '(none)' for the root .datacore/learning/.
    Unknown names are silently skipped.
    """
    backups: list[Path] = []
    for name in space_names:
        if not name or name in ("root", "(none)"):
            dirs = [DATACORE_ROOT / ".datacore" / "learning"]
        else:
            # Match by exact dir name or by suffix after the numeric prefix
            dirs = [
                d / ".datacore" / "learning"
                for d in DATACORE_ROOT.glob("[0-9]-*")
                if d.is_dir() and (d.name == name or d.name.endswith(f"-{name}") or name in d.name)
            ]
            if not dirs:
                continue

        for d in dirs:
            if d.is_dir():
                for f in d.iterdir():
                    if f.is_file():
                        b = rotate(f)
                        if b is not None:
                            backups.append(b)

    return backups


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rotate a .datacore/learning/ file to a timestamped backup.",
        epilog="KEEP last %(prog)s backups per file; older are purged automatically.",
    )
    ap.add_argument("filepath", nargs="?", help="Path to the learning file to rotate")
    ap.add_argument("--all", action="store_true",
                    help="Rotate ALL learning files across root and all spaces")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    if args.all:
        backups = rotate_all()
        if args.verbose:
            for b in backups:
                print(f"  backed up: {b}")
        print(f"rotated {len(backups)} learning file(s)")
        return 0

    if not args.filepath:
        ap.error("provide a filepath or --all")

    path = Path(args.filepath)
    try:
        backup = rotate(path)
    except Exception as e:
        print(f"ERROR: rotation failed for {path}: {e}", file=sys.stderr)
        return 1

    if backup:
        if args.verbose:
            print(f"backed up: {backup}")
        else:
            print(str(backup))
    else:
        if args.verbose:
            print(f"skipped (not yet created): {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
