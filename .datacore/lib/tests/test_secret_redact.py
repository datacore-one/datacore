"""Shared secret masking for agent shell output (task fc51dd1d).

On 2026-10-13 an ad-hoc sed mask matched token/key/secret/password but not an
Authorization Bearer header, and a live access key reached a session log. One
helper, used as a function or as a pipe, replaces the per-command filters.
All values below are fabricated.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from secret_redact import redact  # noqa: E402

JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
       "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkZha2UifQ."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
GLPAT = "glpat-AbCdEfGhIjKlMnOpQrSt"
OPAQUE = "q8Zr2example7wXy1"

CASES = [
    ("Authorization: Bearer q8Zr2example7wXy1", OPAQUE),
    ('"headers": {"Authorization": "Bearer q8Zr2example7wXy1"}', OPAQUE),
    ("authorization: bearer q8Zr2example7wXy1", OPAQUE),
    ("-H 'Authorization: Basic dXNlcjpwYXNzd29yZDEyMw=='", "dXNlcjpwYXNzd29yZDEyMw=="),
    (f"token {JWT} expires", JWT),
    (f"PRIVATE-TOKEN: {GLPAT}", GLPAT),
    (f"https://oauth2:{GLPAT}@git.example.com/group/repo.git", GLPAT),
    ("API_KEY=q8Zr2example7wXy1", OPAQUE),
    ("export GITLAB_TOKEN='q8Zr2example7wXy1'", OPAQUE),
    ('"api_key": "q8Zr2example7wXy1",', OPAQUE),
    ("password: q8Zr2example7wXy1", OPAQUE),
    ("client_secret=q8Zr2example7wXy1&grant_type=x", OPAQUE),
    ("X-API-Key: q8Zr2example7wXy1", OPAQUE),
    ("ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3", "A1b2C3d4E5f6G7h8I9j0K1l2M3"),
    ("sk-proj-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3", "Zx9Yw8Vu7Ts6Rq5Po4Nm3"),
    ("https://user:hunter2pass@db.example.com:5432/app", "hunter2pass"),
]


@pytest.mark.parametrize("text,secret", CASES)
def test_secret_values_are_masked(text, secret):
    out = redact(text)
    assert secret not in out, out
    assert "REDACTED" in out


def test_bearer_keeps_the_header_name():
    assert redact("Authorization: Bearer q8Zr2example7wXy1").startswith("Authorization: Bearer ")


@pytest.mark.parametrize("text", [
    "git -C ~/Data status",
    "token_count: 1234",
    "password_policy: strict",
    "See docs/api/key-rotation.md for how keys rotate.",
    "commit 1c1829e5f0a3b7d9e2c4a6b8d0f1e3a5c7b9d1f2",
])
def test_ordinary_text_is_left_alone(text):
    assert redact(text) == text


def test_redaction_is_idempotent():
    once = redact(f"Authorization: Bearer {OPAQUE} and {GLPAT}")
    assert redact(once) == once


def test_works_as_a_pipe():
    proc = subprocess.run([sys.executable, str(LIB / "secret_redact.py")],
                          input=f"Authorization: Bearer {OPAQUE}\nok line\n",
                          capture_output=True, text=True, check=True)
    assert OPAQUE not in proc.stdout
    assert "ok line" in proc.stdout
