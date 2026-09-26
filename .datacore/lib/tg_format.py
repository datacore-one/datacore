#!/usr/bin/env python3
"""One formatter for every script-built Telegram message.

The rules are the telegram-output-formatting skill's (Telegram is the memo,
the disk is the dossier): bullets are "•", titles are short, nothing is cut
mid-word. Scripts built their messages by hand and cut with `s[:80]`, so the
owner read "…(plur-ai/ent" and lines broken where a column count fell
(2026-09-26). Every sender passes its text through here instead.

    clip(s, n)        shorten at a word boundary, ending in "…"
    normalize(text)   bullets to "•", no markdown emphasis or headings, no runs
                      of blank lines; code blocks are left alone
    html_safe(text)   escape for parse_mode=HTML, keeping <b> <i> <code> <pre>

CLI: `... | python3 tg_format.py [--html]` prints the normalized text, for
shell senders.
"""
from __future__ import annotations

import html
import re
import sys

_BULLET = re.compile(r"^(\s*)[-*+]\s+(?=\S)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")
_KEPT_TAGS = re.compile(r"&lt;(/?)(b|i|u|s|code|pre)&gt;")


def clip(s: str, n: int) -> str:
    """`s` in at most `n` characters, cut at a word boundary, ending in "…"."""
    s = " ".join(str(s).split())
    if len(s) <= n:
        return s
    cut = s[: max(1, n - 1)]
    space = cut.rfind(" ")
    if space >= n // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—(/") + "…"


def normalize(text: str) -> str:
    out: list[str] = []
    in_code = False
    for line in str(text).splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if not in_code:
            line = _BULLET.sub(r"\1• ", line)
            line = _HEADING.sub("", line)
            line = _EMPHASIS.sub(r"\2", line)
            line = line.rstrip()
        if not line.strip() and out and not out[-1].strip():
            continue                      # one blank line between blocks, never more
        out.append(line)
    return "\n".join(out).strip("\n")


def html_safe(text: str) -> str:
    """Escape for Telegram's HTML mode. A bare "<" or "&" in a title made the
    whole send fail, with only a printed warning."""
    return _KEPT_TAGS.sub(r"<\1\2>", html.escape(str(text), quote=False))


def main() -> int:
    text = normalize(sys.stdin.read())
    print(html_safe(text) if "--html" in sys.argv[1:] else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
