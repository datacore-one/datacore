# .datacore/lib/knowledge_lint.py
"""Semantic linting for Datacore knowledge bases.

Checks for:
- Broken links ([[wiki-links]] to a note that does not exist)
- Orphan zettels (no inbound wiki-links)
- Incomplete literature notes (missing required sections)
- Stale content (seedlings not updated in 180+ days)

Contradiction detection is LLM-powered and handled by the
knowledge-linter agent, not this script.

    python3 knowledge_lint.py --all-spaces     # weekly job box-knowledge-lint
    python3 knowledge_lint.py --space ~/Data/0-personal

Exit 1 when a broken link was found (a finding, reported), 0 when none, 2 when
the check could not run. With --state, exit 1 only for broken links that were
not there on the previous run (the first run reports everything once), so the
weekly alert says what rotted this week instead of repeating the backlog.
"""
import json
import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Set


@dataclass
class LintIssue:
    """A semantic lint finding."""
    severity: Literal['error', 'warning', 'info']
    check: str  # broken-link, orphan, completeness, staleness
    path: Path
    message: str
    suggestion: str = ""


REQUIRED_LIT_SECTIONS = ["Summary", "Key Insights"]
WIKI_LINK_RE = re.compile(r'\[\[([^\]]+)\]\]')


def _collect_wiki_links(knowledge_dir: Path) -> Set[str]:
    """Collect all wiki-link targets across all files in the knowledge dir."""
    targets: Set[str] = set()
    for md in knowledge_dir.rglob('*.md'):
        text = md.read_text(encoding='utf-8', errors='replace')
        for match in WIKI_LINK_RE.finditer(text):
            link = match.group(1).strip()
            # Strip pipe alias: [[Target|Display Text]] -> Target
            if '|' in link:
                link = link.split('|')[0].strip()
            targets.add(link)
    return targets


def _link_target(raw: str) -> str:
    """[[Target|Alias]], [[Target#Heading]], [[Target^block]] -> Target."""
    link = raw.split('|')[0]
    link = re.split(r'[#^]', link, maxsplit=1)[0]
    return link.strip()


def check_broken_links(knowledge_dir: Path, resolve_root: Path | None = None) -> List[LintIssue]:
    """Find [[wiki-links]] whose target is no note (KNW-10).

    A target resolves when some .md file under `resolve_root` (default: the
    space holding the knowledge dir) has that stem, case-insensitively, or a
    path ending in it (`[[zettel/Idea]]`). Links into journals, CRM pages and
    other space folders therefore do not count as broken.
    """
    root = resolve_root or knowledge_dir.parent
    stems: Set[str] = set()
    tails: Set[str] = set()
    for f in root.rglob('*'):
        if any(part.startswith('.') for part in f.relative_to(root).parts) or not f.is_file():
            continue
        stems.add(f.name.lower())      # [[competitors.yaml]], [[diagram.png]]
        if f.suffix != '.md':
            continue
        md = f
        stems.add(md.stem.lower())
        rel = md.relative_to(root).with_suffix('').as_posix().lower()
        parts = rel.split('/')
        for i in range(len(parts)):
            tails.add('/'.join(parts[i:]))

    issues: List[LintIssue] = []
    for md in sorted(knowledge_dir.rglob('*.md')):
        if any(part.startswith('.') for part in md.relative_to(knowledge_dir).parts):
            continue
        text = md.read_text(encoding='utf-8', errors='replace')
        # A link shown as code is an example, not a link.
        text = re.sub(r'```.*?```', ' ', text, flags=re.S)
        text = re.sub(r'`[^`\n]*`', ' ', text)
        missing = []
        for match in WIKI_LINK_RE.finditer(text):
            target = _link_target(match.group(1))
            key = target.lower().removesuffix('.md')
            if not key or key in stems or key in tails or target in missing:
                continue
            missing.append(target)
        for target in missing:
            issues.append(LintIssue(
                severity='error',
                check='broken-link',
                path=md,
                message=f"[[{target}]] links to a note that does not exist",
                suggestion=f"Create '{target}', fix the link, or remove it",
            ))
    return issues


def check_orphan_zettels(knowledge_dir: Path) -> List[LintIssue]:
    """Find zettels that no other file links to."""
    zettel_dir = knowledge_dir / "zettel"
    if not zettel_dir.exists():
        return []

    all_links = _collect_wiki_links(knowledge_dir)
    issues: List[LintIssue] = []

    for md in sorted(zettel_dir.glob('*.md')):
        name = md.stem
        if name.startswith('_'):
            continue
        if name not in all_links:
            issues.append(LintIssue(
                severity='warning',
                check='orphan',
                path=md,
                message=f"Zettel '{name}' has no inbound wiki-links",
                suggestion=f"Add [[{name}]] reference in related literature notes or other zettels",
            ))
    return issues


def check_literature_completeness(knowledge_dir: Path) -> List[LintIssue]:
    """Check literature notes for required sections."""
    lit_dir = knowledge_dir / "literature"
    if not lit_dir.exists():
        return []

    issues: List[LintIssue] = []
    for md in sorted(lit_dir.glob('*.md')):
        text = md.read_text(encoding='utf-8', errors='replace')
        headings = set(re.findall(r'^##\s+(.+)', text, re.MULTILINE))

        missing = [s for s in REQUIRED_LIT_SECTIONS if s not in headings]
        if missing:
            issues.append(LintIssue(
                severity='warning',
                check='completeness',
                path=md,
                message=f"Missing sections: {', '.join(missing)}",
                suggestion="Re-run knowledge-extractor on the source to fill gaps",
            ))
    return issues


def check_staleness(knowledge_dir: Path, max_age_days: int = 180) -> List[LintIssue]:
    """Find seedling zettels not updated in max_age_days."""
    zettel_dir = knowledge_dir / "zettel"
    if not zettel_dir.exists():
        return []

    cutoff = time.time() - (max_age_days * 86400)
    issues: List[LintIssue] = []

    for md in sorted(zettel_dir.glob('*.md')):
        text = md.read_text(encoding='utf-8', errors='replace')
        # Only check frontmatter for maturity field
        if text.startswith('---'):
            end = text.find('---', 3)
            if end == -1:
                continue
            frontmatter = text[:end]
        else:
            continue  # No frontmatter = skip
        if 'maturity: seedling' not in frontmatter:
            continue
        mtime = os.path.getmtime(md)
        if mtime < cutoff:
            age_days = int((time.time() - mtime) / 86400)
            issues.append(LintIssue(
                severity='info',
                check='staleness',
                path=md,
                message=f"Seedling zettel unchanged for {age_days} days",
                suggestion="Review and either promote to 'budding' or archive",
            ))
    return issues


def lint_knowledge(knowledge_dir: Path, max_age_days: int = 180) -> List[LintIssue]:
    """Run all lint checks on a knowledge directory."""
    issues: List[LintIssue] = []
    issues.extend(check_broken_links(knowledge_dir))
    issues.extend(check_orphan_zettels(knowledge_dir))
    issues.extend(check_literature_completeness(knowledge_dir))
    issues.extend(check_staleness(knowledge_dir, max_age_days))
    return issues


def format_report(issues: List[LintIssue]) -> str:
    """Format lint issues as a readable report."""
    if not issues:
        return "Knowledge lint: all clear."

    severity_icon = {'error': 'ERR', 'warning': 'WARN', 'info': 'INFO'}
    lines = [f"Knowledge lint: {len(issues)} issue(s)\n"]
    for issue in sorted(issues, key=lambda i: ('error', 'warning', 'info').index(i.severity)):
        lines.append(f"  [{severity_icon[issue.severity]}] {issue.check}: {issue.path.name}")
        lines.append(f"        {issue.message}")
        if issue.suggestion:
            lines.append(f"        -> {issue.suggestion}")
    return '\n'.join(lines)


def _spaces(root: Path) -> List[Path]:
    return [p for p in sorted(root.glob('[0-9]-*')) if (p / '3-knowledge').is_dir()]


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint knowledge bases: broken links, orphans, gaps.")
    ap.add_argument('--space', action='append', type=Path, default=[],
                    help='a space folder (holding 3-knowledge/); repeatable')
    ap.add_argument('--all-spaces', action='store_true', help='every <root>/[0-9]-* space')
    ap.add_argument('--root', type=Path, default=Path(os.environ.get('DATACORE_ROOT', '~/Data')).expanduser())
    ap.add_argument('--max-listed', type=int, default=40, help='findings listed per space and check')
    ap.add_argument('--state', type=Path, default=None,
                    help='remember the broken links here; exit 1 only for new ones')
    args = ap.parse_args(argv)

    spaces = list(args.space) + (_spaces(args.root) if args.all_spaces else [])
    if not spaces:
        print("knowledge-lint: no space given (use --space or --all-spaces)", file=sys.stderr)
        return 2
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S')
    total_broken = total_orphans = 0
    current: Set[str] = set()
    for space in spaces:
        kb = space / '3-knowledge'
        if not kb.is_dir():
            print(f"knowledge-lint: {space.name}: no 3-knowledge/ -- skipped")
            continue
        issues = lint_knowledge(kb)
        by_check: dict = {}
        for issue in issues:
            by_check.setdefault(issue.check, []).append(issue)
        broken, orphans = by_check.get('broken-link', []), by_check.get('orphan', [])
        current.update(f"{i.path.relative_to(space.parent)}::{i.message}" for i in broken)
        total_broken += len(broken)
        total_orphans += len(orphans)
        print(f"knowledge-lint: {space.name}: {len(broken)} broken links, {len(orphans)} orphans, "
              f"{len(by_check.get('completeness', []))} incomplete, "
              f"{len(by_check.get('staleness', []))} stale")
        for check in ('broken-link', 'orphan'):
            listed = by_check.get(check, [])
            for issue in listed[:args.max_listed]:
                print(f"  [{check}] {issue.path.relative_to(space)}: {issue.message}")
            if len(listed) > args.max_listed:
                print(f"  [{check}] ... and {len(listed) - args.max_listed} more")
    if args.state is None:
        print(f"{stamp} knowledge-lint: {total_broken} broken links, {total_orphans} orphans "
              f"in {len(spaces)} space(s)")
        return 1 if total_broken else 0
    try:
        previous = set(json.loads(args.state.read_text(encoding='utf-8')).get('broken', []))
        first = False
    except (OSError, ValueError):
        previous, first = set(), True
    new = sorted(current - previous)
    for key in new[:args.max_listed]:
        where, _, message = key.partition('::')
        print(f"  NEW broken link: {where}: {message}")
    if len(new) > args.max_listed:
        print(f"  ... and {len(new) - args.max_listed} more new")
    try:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.state.with_name(args.state.name + '.tmp')
        tmp.write_text(json.dumps({'at': stamp, 'broken': sorted(current)}), encoding='utf-8')
        os.replace(tmp, args.state)
    except OSError as e:
        print(f"knowledge-lint: could not save {args.state}: {e}", file=sys.stderr)
    print(f"{stamp} knowledge-lint: {total_broken} broken links ({len(new)} new"
          f"{', first run' if first else ''}), {total_orphans} orphans in {len(spaces)} space(s)")
    return 1 if new else 0


if __name__ == '__main__':
    raise SystemExit(main())
