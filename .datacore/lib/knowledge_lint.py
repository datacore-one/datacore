# .datacore/lib/knowledge_lint.py
"""Semantic linting for Datacore knowledge bases.

Checks for:
- Orphan zettels (no inbound wiki-links)
- Incomplete literature notes (missing required sections)
- Stale content (seedlings not updated in 180+ days)

Contradiction detection is LLM-powered and handled by the
knowledge-linter agent, not this script.
"""
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Set


@dataclass
class LintIssue:
    """A semantic lint finding."""
    severity: Literal['error', 'warning', 'info']
    check: str  # orphan, completeness, staleness
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
            targets.add(match.group(1).strip())
    return targets


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
        if 'maturity: seedling' not in text:
            continue
        if os.path.getmtime(md) < cutoff:
            age_days = int((time.time() - os.path.getmtime(md)) / 86400)
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
