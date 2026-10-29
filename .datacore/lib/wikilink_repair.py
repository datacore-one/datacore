#!/usr/bin/env python3
"""
Repair broken wiki-links that can be fixed without judgment.

Uses the same definition of "broken" as structural_integrity.py
(_check_wiki_links): the same file walk, the same link regex, and the same
set of valid targets (lower-cased stem or space-relative path of every
non-hidden .md file). Each broken link is classified:

  RESOLVABLE      exactly one checker-visible .md file matches the link's page
                  part by basename, case-insensitively, treating runs of
                  '-', '_' and spaces as equal, after dropping a stale path
                  and a '.md' suffix
  AMBIGUOUS       several files match that way
  FALSE_POSITIVE  the target exists but the checker misses it (pattern noted:
                  heading, escaped-pipe, md-extension, non-md-file,
                  frontmatter-alias, excluded-location, folder-note,
                  partial-path, in-code)
  UNRESOLVED      no candidate at all

--apply rewrites only RESOLVABLE links, and only the page part of the target:
aliases ("|alias"), headings ("#heading") and a table-escaped pipe ("\\|")
are kept. Files under 2-projects/, node_modules/, .git/, hidden folders and
files git ignores are never edited.

Usage:
    python3 .datacore/lib/wikilink_repair.py --dry-run 1-datafund 3-fds
    python3 .datacore/lib/wikilink_repair.py --classify 1-datafund > links.tsv
    python3 .datacore/lib/wikilink_repair.py --apply 1-datafund
"""

import argparse
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from structural_integrity import PRUNE_DIRS, StructuralIntegrityChecker  # noqa: E402

DATA_ROOT = Path(os.environ.get('DATACORE_ROOT', os.path.expanduser('~/Data')))

# Same pattern as structural_integrity._check_wiki_links.
WIKI_LINK = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')
FENCE = re.compile(r'^[ \t]*(```|~~~)')
INLINE_CODE = re.compile(r'(`+)(?:(?!\1).)+?\1', re.S)
SEP = re.compile(r'[-_\s]+')
PROTECTED_PARTS = {'2-projects', 'node_modules', '.git'}
FOLDER_NOTES = {'_index.md', 'index.md', 'readme.md'}


def norm(name: str) -> str:
    return SEP.sub('-', name.strip().lower()).strip('-')


def hidden(rel: Path) -> bool:
    return any(p.startswith('.') or p == 'node_modules' for p in rel.parts)


def code_spans(text: str):
    """(start, end) offsets of fenced code blocks and inline code."""
    spans, pos, fence_start, fence_mark = [], 0, None, None
    for line in text.splitlines(keepends=True):
        m = FENCE.match(line)
        if m:
            if fence_start is None:
                fence_start, fence_mark = pos, m.group(1)
            elif m.group(1) == fence_mark:
                spans.append((fence_start, pos + len(line)))
                fence_start = None
        pos += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(text)))
    for m in INLINE_CODE.finditer(text):
        if not any(s <= m.start() < e for s, e in spans):
            spans.append((m.start(), m.end()))
    return spans


def split_target(raw: str):
    """raw link target -> (page, suffix) where suffix is '#heading' and/or '\\'."""
    esc = ''
    if raw.endswith('\\'):
        raw, esc = raw[:-1], '\\'
    page, sep, heading = raw.partition('#')
    return page, sep + heading + esc


class SpaceIndex:
    """Everything needed to classify links in one space."""

    def __init__(self, space: Path):
        self.space = space.resolve()
        self.checker = StructuralIntegrityChecker(self.space, quick_mode=True)
        self.valid = set()             # exactly the checker's valid_targets
        self.md_files = []             # checker-visible .md files (rel paths)
        self.by_norm = defaultdict(list)
        for f in self.checker._walk('*.md'):
            rel = f.relative_to(self.space)
            if hidden(rel):
                continue
            self.md_files.append(rel)
            self.valid.add(rel.stem.lower())
            self.valid.add(str(rel.with_suffix('')).lower())
            self.by_norm[norm(rel.stem)].append(rel)
        self.aliases = self._aliases()
        # Everything on disk the checker does not see, for false-positive tests.
        self.other_names, self.dirs, self.excluded_md = set(), set(), set()
        for dirpath, dirnames, filenames in os.walk(self.space):
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
            cur = Path(dirpath).relative_to(self.space)
            if cur != Path('.') and FOLDER_NOTES & {n.lower() for n in filenames}:
                self.dirs.add(cur.name.lower())
                self.dirs.add(str(cur).lower())
            for name in filenames:
                rel = cur / name
                if name.endswith('.md'):
                    if str(rel.with_suffix('')).lower() not in self.valid or hidden(rel):
                        self.excluded_md.add(rel.stem.lower())
                        self.excluded_md.add(str(rel.with_suffix('')).lower())
                else:
                    self.other_names.add(name.lower())
                    self.other_names.add(str(rel).lower())
        self.excluded_md -= self.valid

    def _aliases(self):
        aliases = set()
        for rel in self.md_files:
            try:
                with open(self.space / rel, encoding='utf-8', errors='ignore') as fh:
                    head = fh.read(4000)
            except OSError:
                continue
            if not head.startswith('---'):
                continue
            fm = head[3:].split('\n---', 1)[0]
            m = re.search(r'^aliases?:\s*(.*?)(?=^\S|\Z)', fm, re.M | re.S)
            if not m:
                continue
            for item in re.split(r'[\n,\[\]]', m.group(1)):
                item = item.strip().lstrip('-').strip().strip('"\'')
                if item:
                    aliases.add(item.lower())
        return aliases

    def classify(self, raw: str, in_code: bool):
        """-> (class, detail, new_page_or_None)"""
        if in_code:
            return 'FALSE_POSITIVE', 'in-code', None
        if '\n' in raw:
            return 'UNRESOLVED', 'multiline', None
        page, suffix = split_target(raw)
        p = page.strip().lower()
        if not p:
            return 'UNRESOLVED', 'empty-page', None
        if p in self.valid:
            return 'FALSE_POSITIVE', 'escaped-pipe' if suffix.endswith('\\') else 'heading', None
        if p.endswith('.md') and p[:-3] in self.valid:
            return 'FALSE_POSITIVE', 'md-extension', None
        if p in self.other_names or p.rsplit('/', 1)[-1] in self.other_names:
            return 'FALSE_POSITIVE', 'non-md-file', None
        if p in self.aliases:
            return 'FALSE_POSITIVE', 'frontmatter-alias', None
        if p in self.excluded_md or (p.endswith('.md') and p[:-3] in self.excluded_md):
            return 'FALSE_POSITIVE', 'excluded-location', None
        if p.rstrip('/') in self.dirs:
            return 'FALSE_POSITIVE', 'folder-note', None
        if '/' in p.strip('/'):
            tail = '/' + p.strip('/').removesuffix('.md')
            hits = [r for r in self.md_files if ('/' + str(r.with_suffix('')).lower()).endswith(tail)]
            if len(hits) == 1:
                return 'FALSE_POSITIVE', 'partial-path', None
        base = p.rstrip('/').rsplit('/', 1)[-1]
        if base.endswith('.md'):
            base = base[:-3]
        key = norm(base)
        cands = self.by_norm.get(key, []) if key else []
        if len(cands) == 1:
            return 'RESOLVABLE', str(cands[0]), cands[0].stem
        if len(cands) > 1:
            return 'AMBIGUOUS', '; '.join(sorted(map(str, cands))), None
        return 'UNRESOLVED', '', None

    def broken_links(self, text: str):
        """Yield (match, raw, in_code) for every link the checker calls broken."""
        spans = None
        for m in WIKI_LINK.finditer(text):
            raw = m.group(1)
            target = raw.strip().lower()
            if target.startswith('http') or target.startswith('#') or target in self.valid:
                continue
            if spans is None:
                spans = code_spans(text)
            in_code = any(s <= m.start() < e for s, e in spans)
            yield m, raw, in_code


def git_ignored(space: Path, rels):
    if not rels:
        return set()
    out = subprocess.run(['git', '-C', str(space), 'check-ignore', '--stdin'],
                         input='\n'.join(map(str, rels)), capture_output=True, text=True)
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def rewrite(match, raw, new_page):
    """Return the link text with only the page part of the target replaced."""
    page, suffix = split_target(raw)
    lead = page[:len(page) - len(page.lstrip())]
    trail = page[len(page.rstrip()):]
    new_raw = lead + new_page + trail + suffix
    whole = match.group(0)
    start = match.start(1) - match.start(0)
    return whole[:start] + new_raw + whole[start + len(raw):]


def process_space(space: Path, apply: bool):
    idx = SpaceIndex(space)
    rows = []          # (rel, line, raw, class, detail, protected, new_link)
    per_file = defaultdict(list)
    for rel in idx.md_files:
        try:
            text = (idx.space / rel).read_text(errors='ignore')
        except OSError:
            continue
        for m, raw, in_code in idx.broken_links(text):
            cls, detail, new_page = idx.classify(raw, in_code)
            line = text.count('\n', 0, m.start()) + 1
            new = rewrite(m, raw, new_page) if new_page else ''
            row = [str(rel), line, m.group(0), cls, detail, False, new]
            rows.append(row)
            per_file[rel].append(row)
    protected = {rel for rel in per_file if PROTECTED_PARTS & set(rel.parts)}
    ignored = git_ignored(idx.space, [r for r in per_file if r not in protected])
    protected |= {Path(r) for r in ignored}
    for rel, frows in per_file.items():
        if rel in protected:
            for row in frows:
                row[5] = True

    modified = []
    if apply:
        for rel in per_file:
            if rel in protected or not any(r[3] == 'RESOLVABLE' for r in per_file[rel]):
                continue
            if _apply_file(idx, rel):
                modified.append(str(rel))
    return idx, rows, modified


def _apply_file(idx, rel):
    """Re-read the file now (other writers edit these repos) and rewrite in place."""
    path = idx.space / rel
    try:
        with open(path, encoding='utf-8', newline='') as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    edits = []
    for m, raw, in_code in idx.broken_links(text):
        cls, _, new_page = idx.classify(raw, in_code)
        if cls == 'RESOLVABLE':
            edits.append((m.start(), m.end(), rewrite(m, raw, new_page)))
    if not edits:
        return False
    for start, end, new in reversed(edits):
        text = text[:start] + new + text[end:]
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='print planned rewrites (default)')
    mode.add_argument('--apply', action='store_true', help='rewrite RESOLVABLE links')
    mode.add_argument('--classify', action='store_true', help='TSV of every broken link and its class')
    ap.add_argument('spaces', nargs='+', help='space directories, e.g. 1-teamspace')
    args = ap.parse_args()

    for s in args.spaces:
        space = Path(s) if Path(s).is_absolute() else (Path.cwd() / s if (Path.cwd() / s).exists() else DATA_ROOT / s)
        idx, rows, modified = process_space(space, args.apply)
        name = idx.space.name
        if args.classify:
            for rel, line, link, cls, detail, prot, new in rows:
                print('\t'.join([name, rel, str(line), link, cls, detail,
                                 'protected' if prot else '', new]))
        elif not args.apply:
            for rel, line, link, cls, detail, prot, new in rows:
                if cls == 'RESOLVABLE' and not prot:
                    print(f'{name}/{rel}:{line}\t{link}  ->  {new}')
        else:
            for rel in modified:
                print(f'modified\t{name}/{rel}')
        counts = Counter(r[3] for r in rows)
        fp = Counter(r[4] for r in rows if r[3] == 'FALSE_POSITIVE')
        fixable = sum(1 for r in rows if r[3] == 'RESOLVABLE' and not r[5])
        print(f'# {name}: {len(rows)} broken across {len({r[0] for r in rows})} files | '
              + ', '.join(f'{k} {v}' for k, v in sorted(counts.items()))
              + f' | resolvable in editable files {fixable}'
              + (f' | files modified {len(modified)}' if args.apply else '')
              + ' | false positives: ' + ', '.join(f'{k} {v}' for k, v in fp.most_common()),
              file=sys.stderr)


if __name__ == '__main__':
    main()
