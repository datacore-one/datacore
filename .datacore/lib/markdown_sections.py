"""Locate real Markdown sections without reserializing authored text."""
from __future__ import annotations

from dataclasses import dataclass
import re

from markdown_it import MarkdownIt


@dataclass(frozen=True)
class Section:
    title: str
    start: int
    body: int
    end: int
    level: int


def frontmatter_end(text: str) -> int:
    """Return the exact end of leading YAML metadata, refusing partial metadata."""
    opening = re.match(r'\A(?:\ufeff)?---(?:\r\n|\r|\n)', text)
    if opening:
        closing = re.search(r'(?:\A|(?<=[\r\n]))(?:---|\.\.\.)[ \t]*(?:\r\n|\r|\n|\Z)',
                            text[opening.end():])
        if closing is None:
            raise ValueError('Journal frontmatter is unfinished; source retained')
        return opening.end() + closing.end()
    return 0


def _parse(text: str):
    # Mask metadata without changing offsets or newline sequences.
    end = frontmatter_end(text)
    masked = re.sub(r'[^\r\n]', ' ', text[:end]) + text[end:]
    return MarkdownIt('commonmark').parse(masked)


def sections(text: str) -> list[Section]:
    offsets = [0, *(match.end() for match in re.finditer(r'\r\n|\r|\n', text)), len(text)]
    tokens = _parse(text)
    headings = []
    for index, token in enumerate(tokens):
        if token.type == 'heading_open' and token.level == 0 and token.tag in {'h1', 'h2'}:
            if token.map is None or index + 1 >= len(tokens) or tokens[index + 1].type != 'inline':
                raise ValueError('Markdown heading has no source map; source retained')
            headings.append((tokens[index + 1].content, offsets[token.map[0]],
                             offsets[token.map[1]], int(token.tag[1])))
    return [Section(title, start, body, headings[index + 1][1] if index + 1 < len(headings)
                    else len(text), level)
            for index, (title, start, body, level) in enumerate(headings)]


def _check_title(title: str) -> None:
    if (not isinstance(title, str) or not title or len(title) > 256
            or title != title.strip() or any(c in title for c in '\r\n\0')):
        raise ValueError('Section title must be one nonempty Markdown heading')
    parsed = sections(f'## {title}\n')
    if len(parsed) != 1 or parsed[0].title != title or parsed[0].level != 2:
        raise ValueError('Section title does not round-trip as one heading')


def _check_end_boundary(text: str) -> None:
    marker = 'Datacore append boundary'
    # Ask the same parser whether a new top-level block can start here. This
    # handles unfinished fences and raw HTML without a second syntax parser.
    probe = text + f'\n\n## {marker}\n'
    if not any(section.title == marker and section.start >= len(text) for section in sections(probe)):
        raise ValueError('Journal ends inside an unfinished block; source retained')


def extract_section(text: str, title: str) -> str | None:
    """Extract one exact H2 section; duplicate sections require review."""
    _check_title(title)
    matches = [section for section in sections(text) if section.level == 2 and section.title == title]
    if len(matches) > 1:
        raise ValueError('Duplicate target sections require review; source retained')
    return text[matches[0].start:matches[0].end] if matches else None


def normalize_section(text: str, title: str) -> str:
    """Nest generated content under one H2 without altering literal examples."""
    _check_title(title)
    if not text.strip() or frontmatter_end(text):
        raise ValueError('Section content must be nonempty and contain no frontmatter')
    _check_end_boundary(text)
    headings = sections(text)
    seen_title = False
    replacements = []
    for heading in headings:
        if not seen_title and heading.level == 2 and heading.title == title:
            replacement = ''
            seen_title = True
        else:
            replacement = f'### {heading.title}\n'
        replacements.append((heading.start, heading.body, replacement))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    result = f'## {title}\n\n' + text.strip('\r\n') + '\n'
    actual = sections(result)
    if len(actual) != 1 or actual[0].level != 2 or actual[0].title != title:
        raise ValueError('Generated content does not form one section')
    return result


def append_section(text: str, title: str, content: str) -> str:
    """Append to the last exact top-level H2; preserve every existing character.

    Duplicate headings remain intact. Fenced examples, HTML, indented code and
    nested quotations are not sections. New text follows the existing newline
    convention, while source text is never normalized or re-rendered.
    """
    _check_title(title)
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Journal append requires nonempty content')
    if frontmatter_end(content) or sections(content):
        raise ValueError('Appended content cannot introduce metadata or H1/H2 sections')
    _check_end_boundary(content)
    matches = [section for section in sections(text) if section.level == 2 and section.title == title]
    position = matches[-1].end if matches else len(text)
    if position == len(text):
        _check_end_boundary(text)
    newline = '\r\n' if '\r\n' in text else '\r' if '\r' in text and '\n' not in text else '\n'
    prefix, suffix = text[:position], text[position:]
    separator = '' if prefix.endswith(newline * 2) else newline if prefix.endswith(newline) else newline * 2
    heading = '' if matches else f'## {title}{newline}{newline}'
    ending = '' if content.endswith(newline) else newline
    if suffix:
        ending += newline
    return prefix + separator + heading + content + ending + suffix
