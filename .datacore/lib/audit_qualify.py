#!/usr/bin/env python3
"""Qualify audit findings against the RELEASED pin, not a candidate build.

Item 11 of the 2026-09-14 remaining-work table asks that each source-fixed
finding cite the released pin rather than a candidate bundle. The findings name
their fix commits, so that is checkable rather than assertable: a fix is in the
release exactly when its commit is an ancestor of the released pin, in whichever
repository the commit belongs to.

This reports; it never edits the findings. Its output is evidence a human can
re-derive, which is the point -- the alternative was stamping 51 records with a
claim nobody had made.

WHAT IT FOUND, 2026-09-16: the records do not carry machine-readable commit
references. `runtime_verification` is prose with identifiers concatenated into
it, so almost nothing here resolves to a commit. That is the answer to item 11
rather than a failure of this script: per-item qualification cannot be derived
from the findings as written, and claiming otherwise would be invention.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A git SHA must stand ALONE -- delimited by whitespace or punctuation, never
# carved out of a longer hex run. The findings' prose concatenates identifiers
# without separators ("core04d5e724...8fafe66/NS22bee20", "bundle0427ae49..."),
# and a \b-anchored 7-40 scan slices phantom commits out of them: it reported 38
# findings as "not in the release" against SHAs that never existed in any repo.
SHA = re.compile(r'(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])')
FIELDS = ('remediation', 'specification_change', 'runtime_verification', 'evidence',
          'problem', 'root_cause')


def repos() -> dict[str, Path]:
    found = {'core': ROOT, 'dips': ROOT / '.datacore/dips'}
    for m in sorted((ROOT / '.datacore/modules').glob('*/.git')):
        found['module:' + m.parent.name] = m.parent
    # Findings cite commits from the project repos too -- the MCP server and
    # org-workspace are where several source fixes actually landed. A commit
    # looks "not in the release" only because nobody looked in its repository.
    for space in sorted(ROOT.glob('[0-9]-*/2-projects/*/.git')):
        found['project:' + space.parent.name] = space.parent
    return {k: v for k, v in found.items() if (v / '.git').exists()}


def reachable(repo: Path, spec: str) -> set[str]:
    """Commits reachable from `spec` ('HEAD' for the pin, '--all' for any branch).

    One `rev-list` per repository rather than a `merge-base` per (repo, sha):
    the naive form spawned ~14,000 git processes across 77 repositories and was
    killed for memory. Prefix matching happens here, where it is free.
    """
    r = subprocess.run(['git', '-C', str(repo), 'rev-list', spec],
                       capture_output=True, text=True)
    return set(r.stdout.split()) if r.returncode == 0 else set()


def head(repo: Path) -> str | None:
    r = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--findings', type=Path,
                    default=ROOT / '0-personal/4-outbox/datacore-audit-2026-09-14'
                                   '/Datacore-audit-evidence-2026-09-14/findings.json')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    data = json.loads(args.findings.read_text(encoding='utf-8'))
    items = data if isinstance(data, list) else data.get('findings') or data.get('records') or []
    where = repos()
    print('released pins:')
    for name, path in where.items():
        h = head(path)
        if h and name in ('core', 'dips'):
            print(f'  {name:<8} {h[:7]}')

    # Two indexes, because "not in the release" hides two different facts: a
    # fix sitting on a branch nobody merged, and a string that is no commit at
    # all. The findings' prose runs identifiers together, so a hex scan also
    # yields fragments; conflating those with real unmerged work misreports
    # both. Separate them and each becomes actionable.
    pinned: dict[int, set[str]] = {}
    anywhere: dict[int, set[str]] = {}
    for name, path in where.items():
        for spec, target in (('HEAD', pinned), ('--all', anywhere)):
            for c in reachable(path, spec):
                for n in (7, 8, 12, 40):
                    target.setdefault(n, set()).add(c[:n])

    def released(sha: str) -> bool:
        return sha in pinned.get(len(sha), ())

    def known(sha: str) -> bool:
        return sha in anywhere.get(len(sha), ())

    tally = collections.Counter()
    unresolved: list[tuple[str, list[str]]] = []
    for f in items:
        blob = ' '.join(str(f.get(k) or '') for k in FIELDS)
        shas = sorted({s for s in SHA.findall(blob) if not s.isdigit()})
        if not shas:
            tally['no commit cited'] += 1
            continue
        hits = [s for s in shas if released(s)]
        unmerged = [s for s in shas if not released(s) and known(s)]
        if hits:
            tally['in the released pin'] += 1
            if args.verbose:
                print(f"  {f.get('id'):<5} qualified by {', '.join(h[:7] for h in hits[:3])}")
        elif unmerged:
            tally['fix exists but is UNMERGED'] += 1
            unresolved.append((str(f.get('id')), [s[:7] for s in unmerged[:3]]))
        else:
            tally['no commit resolves (prose fragments)'] += 1

    print('\n' + '\n'.join(f'  {k:<34}{v}' for k, v in sorted(tally.items())))
    if unresolved:
        print('\nfindings whose fix exists but was never merged:')
        for fid, shas in unresolved:
            print(f'  {fid:<5} {", ".join(shas)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
