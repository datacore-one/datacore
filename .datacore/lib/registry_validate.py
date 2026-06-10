#!/usr/bin/env python3
"""Registry validator — agents.yaml / commands.yaml vs files on disk.

Modes:
  report (default)  list registered-but-missing and on-disk-but-unregistered
  --prune           remove registry entries whose source file no longer
                    exists (comment-preserving via ruamel.yaml; a .bak
                    backup is written next to the registry first)
  --json            machine-readable report to stdout

Wired into nightshift Phase 9.5 (Sundays) alongside structural-integrity;
also runnable manually: python3 .datacore/lib/registry_validate.py [--prune]

Context: on 2026-06-10 the agent registry carried ~100 entries whose source
files had moved or been deleted, and ~70 agent files existed unregistered —
nothing ever validated registry-vs-disk (DIP-0016 specifies the registry
but no enforcement existed).

Unregistered files are REPORTED, never auto-registered: registration needs
semantic fields (skills, triggers, spawns) — that is agent-registry-auditor
work, not mechanical validation.
"""

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2]
REGISTRY_DIR = DATA_DIR / '.datacore' / 'registry'

# Dirs scanned for agent definition files. _deprecated/ and _patterns/ are
# intentionally unregistered (graveyard + templates), so they are skipped.
AGENT_GLOBS = [
    '.datacore/agents/*.md',
    '.datacore/modules/*/agents/*.md',
]
SKIP_PARTS = {'_deprecated', '_patterns'}
SKIP_NAMES = {'README.md', 'CODE_OF_CONDUCT.md'}


def _load(path: Path):
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    with path.open('r', encoding='utf-8') as fh:
        return yaml, yaml.load(fh)


def _registered(data, section: str) -> dict:
    entries = data.get(section) or {}
    return {str(name): (entry or {}) for name, entry in entries.items()}


def _source_exists(entry: dict) -> bool:
    src = entry.get('source')
    if not src:
        return False
    return (DATA_DIR / str(src)).exists()


def validate(registry_path: Path, section: str) -> dict:
    _, data = _load(registry_path)
    entries = _registered(data, section)
    missing = sorted(name for name, e in entries.items()
                     if not _source_exists(e))
    deprecated = sorted(
        name for name, e in entries.items()
        if e.get('deprecated') or 'DEPRECATED' in str(e.get('description', '')))
    registered_sources = set()
    for e in entries.values():
        src = e.get('source')
        if src:
            try:
                registered_sources.add((DATA_DIR / str(src)).resolve())
            except OSError:
                pass

    unregistered = []
    if section == 'agents':
        for pattern in AGENT_GLOBS:
            for f in DATA_DIR.glob(pattern):
                if SKIP_PARTS & set(f.parts) or f.name in SKIP_NAMES:
                    continue
                try:
                    if f.resolve() not in registered_sources:
                        unregistered.append(str(f.relative_to(DATA_DIR)))
                except OSError:
                    continue
        unregistered.sort()

    return {
        'registry': str(registry_path.relative_to(DATA_DIR)),
        'section': section,
        'total_entries': len(entries),
        'missing_source': missing,
        'deprecated_registered': deprecated,
        'unregistered_files': unregistered,
    }


def prune(registry_path: Path, section: str) -> list:
    """Remove entries with missing source files. Returns pruned names."""
    yaml, data = _load(registry_path)
    entries = data.get(section) or {}
    to_prune = [name for name in list(entries.keys())
                if not _source_exists(entries[name] or {})]
    if not to_prune:
        return []
    backup = registry_path.with_suffix(
        f'.yaml.bak-{date.today().isoformat()}')
    shutil.copy2(registry_path, backup)
    for name in to_prune:
        del entries[name]
    if 'updated' in data:
        data['updated'] = date.today().isoformat()
    with registry_path.open('w', encoding='utf-8') as fh:
        yaml.dump(data, fh)
    return to_prune


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--prune', action='store_true',
                        help='Remove entries whose source file is missing.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    targets = [
        (REGISTRY_DIR / 'agents.yaml', 'agents'),
        (REGISTRY_DIR / 'commands.yaml', 'commands'),
    ]
    reports = []
    for path, section in targets:
        if not path.exists():
            continue
        report = validate(path, section)
        if args.prune and report['missing_source']:
            report['pruned'] = prune(path, section)
        reports.append(report)

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for r in reports:
            print(f"[{r['registry']}] {r['total_entries']} entries | "
                  f"missing-source: {len(r['missing_source'])} | "
                  f"deprecated-still-registered: {len(r['deprecated_registered'])} | "
                  f"unregistered-files: {len(r['unregistered_files'])}"
                  + (f" | PRUNED: {len(r['pruned'])}" if 'pruned' in r else ''))
            for name in r['missing_source'][:20]:
                print(f"  missing: {name}")
            if len(r['missing_source']) > 20:
                print(f"  ... and {len(r['missing_source']) - 20} more")
            for f in r['unregistered_files'][:15]:
                print(f"  unregistered: {f}")
            if len(r['unregistered_files']) > 15:
                print(f"  ... and {len(r['unregistered_files']) - 15} more")

    any_drift = any(r['missing_source'] or r['unregistered_files']
                    for r in reports)
    return 1 if (any_drift and not args.prune) else 0


if __name__ == '__main__':
    sys.exit(main())
