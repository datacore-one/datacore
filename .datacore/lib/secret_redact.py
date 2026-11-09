#!/usr/bin/env python3
"""Mask secret values in text before an agent prints it.

One helper instead of a hand-rolled sed per command (an ad-hoc mask once
matched token/key/secret/password but not an Authorization Bearer header, and a
live key reached a session log). Covers auth headers (Bearer, Basic, Token,
API-key headers), JWTs, provider tokens (GitLab glpat-, GitHub, OpenAI-style
sk-, AWS, Slack), credentials inside URLs, and key=value / key: value pairs
whose key names a secret.

    some_command 2>&1 | python3 .datacore/lib/secret_redact.py
    from secret_redact import redact
"""
from __future__ import annotations

import re
import sys

MARK = "[REDACTED]"

_SECRET_NAME = (r"[A-Za-z0-9_.\-]*(?:api[_\-]?key|apikey|secret|token|passw(?:or)?d|passwd|pwd|"
                r"private[_\-]?key|access[_\-]?key|auth|credential|session[_\-]?key)[A-Za-z0-9_\-]*")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer <v> / Basic <v> / Token <v>
    (re.compile(r"(?i)\b(bearer|basic|token)(\s+)(?!\[REDACTED\])[A-Za-z0-9._~+/=\-]{8,}"),
     r"\1\2" + MARK),
    # JWT
    (re.compile(r"(?<![A-Za-z0-9_\-.])eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), MARK),
    # provider tokens
    (re.compile(r"\b(?:glpat|gldt|glrt|glptt)-[A-Za-z0-9_\-]{16,}"), MARK),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), MARK),
    (re.compile(r"(?<![A-Za-z0-9_\-])sk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_\-]{16,}"), MARK),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), MARK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), MARK),
    # scheme://user:secret@host
    (re.compile(r"([a-z][a-z0-9+.\-]*://[^\s:/@]+:)(?!\[REDACTED\])[^\s@/]+@"), r"\1" + MARK + "@"),
    # header-style  X-API-Key: v  /  PRIVATE-TOKEN: v
    (re.compile(r"(?i)\b((?:x-)?(?:api-key|auth-token|private-token|access-token)\s*:\s*)"
                r"(?!\[REDACTED\])[\"']?[^\s\"',;]{6,}[\"']?"), r"\1" + MARK),
    # key=value, key: value, "key": "value" where key names a secret
    (re.compile(r"(?i)(\b" + _SECRET_NAME + r"[\"']?\s*[:=]\s*[\"']?)"
                r"(?!\[REDACTED\])(?=[^\s\"',;&]*[0-9])(?=[^\s\"',;&]*[A-Za-z])[^\s\"',;&]{6,}"),
     r"\1" + MARK),
]


def redact(text: str) -> str:
    """The text with every recognised secret value replaced by [REDACTED]."""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def main() -> int:
    for line in sys.stdin:
        sys.stdout.write(redact(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
