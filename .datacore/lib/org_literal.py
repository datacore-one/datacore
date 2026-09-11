"""Render external prose as literal Org data, never as document structure."""
import json


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
