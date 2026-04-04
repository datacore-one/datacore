# .datacore/lib/journal_scanner.py
"""Scan journal entries for sections worth promoting to permanent knowledge.

Uses heuristic scoring (no LLM) to identify sections containing:
- Technical findings, root causes, architecture decisions
- Research summaries with references
- Patterns, insights, or reusable knowledge

Returns scored sections for the knowledge-promoter agent to route
through knowledge-extractor.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class JournalSection:
    """A section extracted from a journal entry."""
    title: str
    content: str  # full text including title
    level: int    # heading level (2 = ##, 3 = ###)
    score: float = 0.0


# Positive signals — content likely worth promoting
POSITIVE_PATTERNS = [
    (r'\b(root cause|finding|diagnosed|key insight|key takeaway)\b', 0.15),
    (r'\binsight\b', 0.08),
    (r'\b(architecture|design decision|trade-?off|pattern)\b', 0.12),
    (r'\b(discovered|learned|realized|important)\b', 0.08),
    (r'\[\[.+?\]\]', 0.10),           # wiki-links suggest cross-references
    (r'(?:^|\n)\s*-\s+.+(?:\n\s*-\s+.+){2,}', 0.08),  # 3+ bullet list
    (r'references?:|sources?:|see also:', 0.10),
    (r'```', 0.05),                    # code blocks
    (r'\b(whitepaper|paper|RFC|spec|DIP)\b', 0.08),
]

# Negative signals — content unlikely to be promotable
NEGATIVE_PATTERNS = [
    (r'\b(standup|sync|quick call|nothing actionable)\b', -0.20),
    (r'\b(TODO|WIP|in progress|will do)\b', -0.05),
]

# Length bonus: longer sections more likely to contain substance
MIN_WORDS_FOR_BONUS = 50
WORD_BONUS_FACTOR = 0.002  # per word above minimum, capped


def extract_sections(text: str) -> List[JournalSection]:
    """Split journal markdown into sections by ## or ### headings."""
    lines = text.split('\n')
    sections: List[JournalSection] = []
    current_title = ""
    current_lines: List[str] = []
    current_level = 0

    for line in lines:
        match = re.match(r'^(#{2,3})\s+(.+)', line)
        if match:
            if current_title:
                sections.append(JournalSection(
                    title=current_title,
                    content='\n'.join(current_lines).strip(),
                    level=current_level,
                ))
            current_level = len(match.group(1))
            current_title = match.group(2).strip()
            current_lines = [line]
        elif current_title:
            current_lines.append(line)

    if current_title:
        sections.append(JournalSection(
            title=current_title,
            content='\n'.join(current_lines).strip(),
            level=current_level,
        ))

    return sections


def score_section(section: JournalSection) -> float:
    """Score a section for promotion worthiness (0.0 - 1.0)."""
    score = 0.0
    text = section.content.lower()

    for pattern, weight in POSITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            score += weight

    for pattern, weight in NEGATIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight  # weight is negative

    # Length bonus
    word_count = len(text.split())
    if word_count > MIN_WORDS_FOR_BONUS:
        score += min((word_count - MIN_WORDS_FOR_BONUS) * WORD_BONUS_FACTOR, 0.15)

    return max(0.0, min(1.0, score))


def scan_journal(text: str, threshold: float = 0.4) -> List[JournalSection]:
    """Scan journal text and return sections scoring above threshold."""
    sections = extract_sections(text)
    for section in sections:
        section.score = score_section(section)
    return sorted(
        [s for s in sections if s.score >= threshold],
        key=lambda s: s.score,
        reverse=True,
    )


def scan_journal_file(path: Path, threshold: float = 0.4) -> List[JournalSection]:
    """Scan a journal file and return promotable sections."""
    return scan_journal(path.read_text(encoding='utf-8'), threshold=threshold)
