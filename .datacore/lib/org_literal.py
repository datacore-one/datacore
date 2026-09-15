"""Render external prose as literal Org data, never as document structure."""
import json
import re


def require_resolved_source(source: str) -> None:
    """Refuse Git conflict syntax outside literal Org blocks.

    Parsing both sides as ordinary tasks can grant authority to an unresolved
    branch or rewrite it during an unrelated save. Keep examples untouched.
    """
    block = None
    for line in source.splitlines():
        if block is not None:
            if re.fullmatch(r'\s*#\+end_' + re.escape(block) + r'\s*', line, re.I):
                block = None
            continue
        begin = re.match(r'\s*#\+begin_([A-Za-z0-9_]+)(?:\s|$)', line, re.I)
        if begin:
            block = begin.group(1)
            continue
        if re.match(r'^(?:<{7,}|>{7,}|\|{7,})(?:\s|$)|^={7,}\s*$', line):
            raise ValueError('unresolved Org conflict requires explicit reconciliation')


def scalar(value: str) -> str:
    """One line, preserving controls as JSON escapes and disabling markup.

    Ordinary identifiers are unchanged. The escaped representation can be
    decoded by wrapping it in JSON quotes, so no source characters are lost.
    """
    if not isinstance(value, str):
        raise ValueError("Org text must be a string")
    text = json.dumps(value, ensure_ascii=False)[1:-1]
    for char in "[]<>{}":
        text = text.replace(char, "\\u%04x" % ord(char))
    return text


def prose(value: str) -> list[str]:
    """Org fixed-width text cannot introduce headings, drawers or directives."""
    return [": " + line for line in value.splitlines()]
