#!/usr/bin/env python3
"""Regenerate the Modules table in .datacore/CATALOG.md from live data.

Reads every .datacore/modules/*/module.yaml + the module repo's git
remote, and rewrites the section between the markers:

    <!-- MODULES-TABLE:START -->  ...  <!-- MODULES-TABLE:END -->

(markers are appended with a fresh section if absent). Run manually or
from the weekly nightshift maintenance phase. Part of repo-strategy
Option A (2026-06-10): CATALOG.md is the published module registry —
it must reflect reality, not hand-edits.
"""

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parents[2]
CATALOG = DATA_DIR / '.datacore' / 'CATALOG.md'
MODULES = DATA_DIR / '.datacore' / 'modules'
START, END = '<!-- MODULES-TABLE:START -->', '<!-- MODULES-TABLE:END -->'
# Modules that must not appear in this PUBLIC catalog (private repos, personal
# accounts): `exclude:` list in the gitignored config/catalog.local.yaml, or the
# comma-separated CATALOG_EXCLUDE env var.
EXCLUDE_FILE = DATA_DIR / '.datacore' / 'config' / 'catalog.local.yaml'


def excluded() -> set[str]:
    names = {n.strip() for n in os.environ.get('CATALOG_EXCLUDE', '').split(',') if n.strip()}
    if EXCLUDE_FILE.exists():
        doc = yaml.safe_load(EXCLUDE_FILE.read_text(encoding='utf-8')) or {}
        names |= {str(n) for n in (doc.get('exclude') or [])}
    return names


def module_rows() -> list[str]:
    rows = []
    skip = excluded()
    for mdir in sorted(MODULES.iterdir()):
        myaml = mdir / 'module.yaml'
        if not myaml.exists():
            continue
        try:
            meta = yaml.safe_load(myaml.read_text(encoding='utf-8')) or {}
        except yaml.YAMLError:
            meta = {}
        name = meta.get('name', mdir.name)
        if name in skip or mdir.name in skip:
            continue
        version = meta.get('version', '?')
        desc = str(meta.get('description', ''))[:80].replace('|', '/')
        remote = ''
        last = ''
        if (mdir / '.git').exists():
            r = subprocess.run(['git', '-C', str(mdir), 'remote',
                                'get-url', 'origin'],
                               capture_output=True, text=True)
            remote = r.stdout.strip()
            remote = re.sub(r'^git@github\.com:', '', remote)
            remote = re.sub(r'^https://github\.com/', '', remote)
            remote = re.sub(r'\.git$', '', remote)
            lr = subprocess.run(['git', '-C', str(mdir), 'log', '-1',
                                 '--format=%ad', '--date=short'],
                                capture_output=True, text=True)
            last = lr.stdout.strip()
        if remote and 'github.com' not in r.stdout:
            remote, host = '', 'self-hosted'   # never publish a private server's URL
        else:
            host = 'local-only' if not remote else remote.split('/')[0]
        rows.append(f"| {name} | {version} | {desc} | "
                    f"{remote or '—'} | {host} | {last or '—'} |")
    return rows


def main() -> int:
    rows = module_rows()
    table = '\n'.join([
        START,
        f"_Generated {date.today().isoformat()} by "
        f"`.datacore/lib/catalog_update.py` — do not hand-edit._",
        '',
        '| Module | Version | Description | Repo | Host | Last commit |',
        '|--------|---------|-------------|------|------|-------------|',
        *rows,
        END,
    ])
    text = CATALOG.read_text(encoding='utf-8')
    if START in text and END in text:
        text = re.sub(re.escape(START) + '.*?' + re.escape(END),
                      table, text, flags=re.DOTALL)
    else:
        text = text.rstrip() + '\n\n## Modules (registry)\n\n' + table + '\n'
    CATALOG.write_text(text, encoding='utf-8')
    print(f"[catalog] {len(rows)} modules written to {CATALOG.relative_to(DATA_DIR)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
