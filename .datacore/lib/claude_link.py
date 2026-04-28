#!/usr/bin/env python3
"""Symlink datacore artifacts into ~/.claude/ for native Claude Code discovery.

Convention: <module>:<name> for module artifacts, <name> for top-level.
Idempotent. Tracks managed links in ~/.claude/.datacore-link.registry.json.

Usage:
  python3 claude_link.py             # sync (idempotent)
  python3 claude_link.py --dry-run   # report planned changes
  python3 claude_link.py --force     # bypass mtime fast-path
  python3 claude_link.py --check     # verify registry vs filesystem
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", HOME / "Data" / ".datacore"))
CLAUDE_ROOT = HOME / ".claude"
REGISTRY = CLAUDE_ROOT / ".datacore-link.registry.json"
STAMP = CLAUDE_ROOT / ".datacore-link.stamp"
LOG = CLAUDE_ROOT / "datacore-link.log"
MODULE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
DATA_ROOT = HOME / "Data"


def _load_registry() -> set[str]:
    if not REGISTRY.exists():
        return set()
    try:
        return set(json.loads(REGISTRY.read_text()))
    except Exception:
        return set()


def _save_registry(names: set[str]) -> None:
    REGISTRY.write_text(json.dumps(sorted(names), indent=2))


def _safe_source(target: Path) -> bool:
    try:
        real = target.resolve(strict=True)
    except OSError:
        return False
    return str(real).startswith(str(DATA_ROOT.resolve()))


def collect_desired() -> tuple[dict[Path, Path], list[str]]:
    desired: dict[Path, Path] = {}
    collisions: list[str] = []

    # Top-level (no prefix)
    for kind in ("agents", "commands"):
        src = DATACORE_ROOT / kind
        if not src.exists():
            continue
        for f in sorted(src.glob("*.md")):
            # Skip top-level files that already point into a module
            if f.is_symlink() and "/.datacore/modules/" in str(f.resolve()):
                continue
            if not _safe_source(f):
                print(f"warn: unsafe source skipped: {f}", file=sys.stderr)
                continue
            link = CLAUDE_ROOT / kind / f.name
            desired[link] = f

    # Modules
    modules = DATACORE_ROOT / "modules"
    if modules.exists():
        for mod_dir in sorted(modules.iterdir()):
            if not mod_dir.is_dir():
                continue
            mod = mod_dir.name
            if not MODULE_NAME_RE.match(mod):
                print(f"warn: invalid module name: {mod}", file=sys.stderr)
                continue

            # Agents and commands (file symlinks)
            for kind in ("agents", "commands"):
                src = mod_dir / kind
                if not src.exists():
                    continue
                for f in sorted(src.glob("*.md")):
                    if not _safe_source(f):
                        continue
                    link = CLAUDE_ROOT / kind / f"{mod}:{f.stem}.md"
                    if link in desired:
                        collisions.append(f"{link} (skipped, first-win)")
                    else:
                        desired[link] = f

            # Skills — both flat (<name>.md) and dir (<name>/SKILL.md) shapes
            skills = mod_dir / "skills"
            if not skills.exists():
                continue
            for entry in sorted(skills.iterdir()):
                if entry.is_dir() and (entry / "SKILL.md").exists():
                    if not _safe_source(entry):
                        continue
                    link = CLAUDE_ROOT / "skills" / f"{mod}:{entry.name}"
                    if link in desired:
                        collisions.append(f"{link} (skipped)")
                    else:
                        desired[link] = entry
                elif entry.suffix == ".md" and entry.is_file():
                    if not _safe_source(entry):
                        continue
                    link = CLAUDE_ROOT / "skills" / f"{mod}:{entry.stem}.md"
                    if link in desired:
                        collisions.append(f"{link} (skipped)")
                    else:
                        desired[link] = entry

    if collisions:
        with LOG.open("a") as f:
            ts = datetime.utcnow().isoformat()
            for c in collisions:
                f.write(f"{ts} collision {c}\n")
    return desired, collisions


def sync(dry_run: bool = False, force: bool = False) -> int:
    # Fast path: skip if modules unchanged since last stamp
    if not force and STAMP.exists() and (DATACORE_ROOT / "modules").exists():
        if (DATACORE_ROOT / "modules").stat().st_mtime <= STAMP.stat().st_mtime:
            # Still useful to confirm no-op
            print("datacore-link: no changes (mtime fast-path)")
            return 0

    for kind in ("agents", "commands", "skills"):
        (CLAUDE_ROOT / kind).mkdir(parents=True, exist_ok=True)

    desired, collisions = collect_desired()
    registry = _load_registry()
    new_registry: set[str] = set()
    created: list[Path] = []
    updated: list[Path] = []
    removed: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    for link, target in desired.items():
        rel = link.relative_to(CLAUDE_ROOT).as_posix()
        new_registry.add(rel)

        # Already-correct?
        if link.is_symlink():
            try:
                if link.resolve(strict=True) == target.resolve(strict=True):
                    continue
            except (OSError, RuntimeError):
                pass

        if link.exists() or link.is_symlink():
            if rel not in registry:
                skipped.append((link, "non-managed file exists"))
                continue
            if not dry_run:
                try:
                    link.unlink()
                except FileNotFoundError:
                    pass
            updated.append(link)

        if not dry_run:
            link.symlink_to(target.absolute())
        if link not in updated:
            created.append(link)

    # Cleanup: managed links no longer desired
    for rel in sorted(registry - new_registry):
        link = CLAUDE_ROOT / rel
        if link.is_symlink() and not dry_run:
            try:
                link.unlink()
            except FileNotFoundError:
                pass
        removed.append(link)

    if not dry_run:
        _save_registry(new_registry)
        STAMP.touch()

    print(
        f"datacore-link: +{len(created)} ~{len(updated)} -{len(removed)} skip={len(skipped)}"
    )
    for link, reason in skipped:
        print(f"  skip {link}: {reason}", file=sys.stderr)
    if collisions:
        return 2
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    if args.check:
        # Verify registry matches filesystem
        reg = _load_registry()
        bad = [r for r in reg if not (CLAUDE_ROOT / r).is_symlink()]
        if bad:
            print(f"check: {len(bad)} stale registry entries", file=sys.stderr)
            for r in bad:
                print(f"  stale: {r}", file=sys.stderr)
            sys.exit(1)
        print(f"check: OK ({len(reg)} entries)")
        sys.exit(0)
    sys.exit(sync(dry_run=args.dry_run, force=args.force))
