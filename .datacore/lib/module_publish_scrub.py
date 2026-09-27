#!/usr/bin/env python3
"""Scrub a module's TRACKED files of anything that identifies its author.

Why this is not just a secret scan. `audit_public_repos.py` catches keys and
known customer names. It does not catch the thing that actually leaks from a
personal tool: the shape of one person's inbox. A classification rule listing
`coinmetrics@substack.com`, `notboring@substack.com` and `veradiverdict@substack.com`
contains no secret and is a behavioural fingerprint — it says who you read.

So the rule here is stricter than the denylist: EVERY email address that is not
already a synthetic one is replaced, service senders included. A list of which
services someone uses identifies them as surely as their own address does.

Replacements preserve SHAPE, so classification rules and docs still read
correctly — a newsletter sender stays a newsletter sender, at example.com.
Sender lists that ship this way are examples the installing user replaces with
their own; a module ships code, never the author's data (ENG-2026-09-07-023).

    python3 module_publish_scrub.py <module> [--apply]   # default: dry run
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

MODULES = Path.home() / "Data/.datacore/modules"
EMAIL = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}')
SYNTH = re.compile(r'(\.|@)example\.(com|org|net)|\.example$|\.invalid|\.test|\.localhost|@localhost', re.I)  # .example/.invalid/.test are RFC-2606 reserved
# Not addresses at all: SSH remotes and CI identities. Rewriting `git@github.com`
# silently breaks every clone URL in the docs, which is a worse bug than the leak.
NOT_EMAIL = re.compile(r'^(git@github\.com|git@gitlab\.com|github-actions(\[bot\])?@github\.com)$', re.I)
HOME = re.compile(r'(?:^|[\s\'"`=(])/(?:Users|home)/[a-zA-Z0-9_.-]+/')  # anchored: an API path like /2/users/by/username is not a home directory
TEXT = {'.py', '.md', '.yaml', '.yml', '.json', '.js', '.ts', '.mjs', '.sh', '.txt', '.html'}

# Shape-preserving buckets. The local part signals the ROLE so rules stay legible.
BUCKETS = (
    (re.compile(r'(newsletter|news@|substack|beehiiv|economist|techcrunch|coindesk|'
                r'theblock|defiant|rundown|gwei|platformer|notboring|veradiverdict|'
                r'farnamstreet|hbr|weekinethereum|coinmetrics)', re.I), 'newsletter{n}@newsletter.example.com'),
    (re.compile(r'(noreply|no-reply|donotreply|do-not-reply|notifications?@|notify@)', re.I),
     'noreply{n}@service.example.com'),
    (re.compile(r'(invoice|billing|accounting|statements|stripe)', re.I), 'billing{n}@vendor.example.com'),
    (re.compile(r'(support|team@|hello@|info@|contact@)', re.I), 'support{n}@vendor.example.com'),
    (re.compile(r'(calendar|invite|luma)', re.I), 'calendar{n}@service.example.com'),
)
PEOPLE = ['alice', 'bob', 'carol', 'dave', 'erin', 'frank', 'grace', 'heidi',
          'ivan', 'judy', 'mallory', 'niaj', 'olivia', 'peggy', 'rupert', 'sybil',
          'trent', 'victor', 'walter', 'wendy']

# An address is not the only thing that names someone. A persona string, a
# colleague's handle in an actor map, a real snippet left in a test fixture and
# a vendor domain in a tagging rule all identify the author just as well.
#
# WHOSE identities those are is the installing author's own list, so it is not
# in this file (INS-3: shipped code names nobody of ours) -- it is
# `identities:` in the gitignored config/publish-scrub.local.yaml, pairs of
# [real, replacement], applied in order. Keep them longest-first so
# 'first.last' is consumed before 'first'. No file: only addresses and home
# directories are scrubbed.
IDENTITIES_FILE = Path(__file__).resolve().parent.parent / "config" / "publish-scrub.local.yaml"


def load_identities(path: Path = IDENTITIES_FILE) -> list[tuple[str, str]]:
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    pairs = data.get("identities") or [] if isinstance(data, dict) else []
    out = []
    for pair in pairs:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, str) and x for x in pair)):
            raise SystemExit(f"{path}: every identity must be a [real, replacement] pair of strings")
        out.append((pair[0], pair[1]))
    return out


IDENTITIES = load_identities()


def tracked(mod: Path) -> list[str]:
    out = subprocess.run(['git', '-C', str(mod), 'ls-files'], capture_output=True, text=True)
    return [f for f in out.stdout.split('\n') if f.strip()]


def build_map(mod: Path, files: list[str]) -> dict[str, str]:
    found: set[str] = set()
    for rel in files:
        p = mod / rel
        if not p.is_file() or p.suffix.lower() not in TEXT:
            continue
        try:
            found |= {e for e in EMAIL.findall(p.read_text(errors='ignore'))
                      if not SYNTH.search(e) and not NOT_EMAIL.match(e)}
        except OSError:
            continue

    mapping, counts, person = {}, {}, 0
    for addr in sorted(found):
        for rx, tmpl in BUCKETS:
            if rx.search(addr):
                counts[tmpl] = counts.get(tmpl, 0) + 1
                mapping[addr] = tmpl.format(n=counts[tmpl])
                break
        else:
            mapping[addr] = f'{PEOPLE[person % len(PEOPLE)]}@example.com'
            person += 1
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    mod = MODULES / a.module
    if not (mod / '.git').exists():
        sys.exit(f'{mod} is not a git repo')

    files = tracked(mod)
    mapping = build_map(mod, files)
    changed = {}

    for rel in files:
        p = mod / rel
        if not p.is_file() or p.suffix.lower() not in TEXT:
            continue
        try:
            orig = p.read_text(errors='ignore')
        except OSError:
            continue
        text = orig
        for real, fake in mapping.items():
            text = text.replace(real, fake)
        # An absolute home path names the author even with no address in sight.
        text = HOME.sub('/home/user/', text)
        for real, fake in IDENTITIES:
            text = text.replace(real, fake)
        if text != orig:
            changed[rel] = sum(1 for r in mapping if r in orig) + len(HOME.findall(orig))
            if a.apply:
                p.write_text(text)

    print(f'{a.module}: {len(mapping)} address(es) mapped, {len(changed)} file(s) '
          f'{"rewritten" if a.apply else "would change"}')
    for real, fake in sorted(mapping.items())[:60]:
        print(f'    {real:46} -> {fake}')
    if len(mapping) > 60:
        print(f'    ... +{len(mapping) - 60} more')
    if not a.apply:
        print('\n  dry run — pass --apply to write')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
