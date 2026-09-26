"""INS-3: "A fresh install names nothing from this fleet by default, no machine,
user, path or space of ours."

Kind: deterministic (static scan of what a fresh install ships, plus one
behavioural probe on a fresh install).
  - Every tracked file a fresh install runs or reads as configuration
    (.datacore/lib code, .datacore/config, .datacore/templates, .datacore/hooks,
    the *.example files) is scanned for this fleet's names in CODE and VALUES:
    string literals in Python (docstrings excluded), non-comment text in shell,
    values in YAML/JSON. Comments are the allow-list: they may tell history.
    Names: machines (winston, nightshift as a host, hermes, plur-claw, miles,
    tris), the user (gregor, and his home directory on linux and mac), our paths (/root/Data)
    and our spaces (1-datafund, 2-datacore, 5-plur, 6-meridian, 7-megaphone,
    8-firm, 9-practice, 3-fds, 4-forge).
  - Behaviour: on a fresh install (git archive HEAD, INSTALL.md followed) the job
    verifier finds no job of ours to check for this machine.

Seeded failure: a default like DEFAULT_SEQUENCER = "winston", git_relay HOSTS,
the owner's linux home in a setup script, or the tracked manifest's mac-* jobs being
verified on a stranger's laptop.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tokenize
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

DATA = F.REAL
NAMES = re.compile(
    r"\b(?:winston|miles|tris|gregor|hermes|plur-claw)\b"
    r"|/(?:home|Users)/greg[o]r|/root/Data\b"
    r"|\b(?:1-datafund|2-datacore|3-fds|4-forge|5-plur|6-meridian|7-megaphone|8-firm|9-practice)\b",
    re.I)
NIGHTSHIFT_HOST = re.compile(r"""^nightshift$|\bssh\s+(?:-\S+\s+)*nightshift\b|\bnightshift:[~/]""")
SCOPES = (".datacore/lib/", ".datacore/config/", ".datacore/templates/", ".datacore/hooks/")
SKIP = ("/tests/", "/4-archive/", "/__pycache__/")


def _shipped() -> list[str]:
    out = subprocess.run(["git", "-C", str(DATA), "ls-files"], capture_output=True, text=True,
                         timeout=30).stdout.splitlines()
    keep = []
    for f in out:
        if f.endswith(".example") or (f.startswith(SCOPES) and not any(s in "/" + f for s in SKIP)):
            if f.endswith((".py", ".sh", ".yaml", ".yml", ".json", ".example", ".toml", ".env")) or "/hooks/" in f:
                keep.append(f)
    return keep


def _py_values(text: str) -> list[tuple[int, str]]:
    out, prev = [], tokenize.NEWLINE
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING:
                is_doc = prev in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.NL) \
                    and tok.string.lstrip("rbuRBUfF")[:3] in ('"""', "'''")
                if not is_doc:
                    out.append((tok.start[0], tok.string))
            if tok.type not in (tokenize.COMMENT, tokenize.NL):
                prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def _uncommented(text: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if not s or s.startswith(("#", "//")):
            continue
        out.append((n, re.sub(r"\s+#\s.*$", "", line)))
    return out


def _hits(rel: str) -> list[str]:
    p = DATA / rel
    try:
        text = p.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    chunks = _py_values(text) if rel.endswith(".py") else _uncommented(text)
    found = []
    for n, chunk in chunks:
        body = chunk.strip("'\"") if rel.endswith(".py") else chunk
        m = NAMES.search(chunk) or NIGHTSHIFT_HOST.search(body.strip())
        if m:
            found.append(f"{rel}:{n}: {m.group(0)}")
    return found


def test_the_scan_covers_the_install():
    files = _shipped()
    assert len(files) > 300, f"only {len(files)} files scanned: the scope is broken"
    assert _hits.__code__ and _py_values('X = "winston"\n'), "the value scanner sees literals"
    assert not _py_values('def f():\n    """winston docstring"""\n'), "docstrings are allowed"


def test_nothing_of_ours_is_a_default():
    hits = [h for f in _shipped() for h in _hits(f)]
    by_name = Counter(h.rsplit(": ", 1)[1].lower() for h in hits)
    by_file = Counter(h.split(":", 1)[0] for h in hits)
    assert hits == [], (
        f"{len(hits)} fleet names in {len(by_file)} shipped files: {dict(by_name.most_common())}\n"
        "worst files: " + json.dumps(by_file.most_common(12)) + "\nfirst: " + "\n".join(hits[:25]))


def test_a_fresh_install_verifies_no_job_of_ours(tmp_path):
    inst = F.follow_guide(tmp_path)
    for machine in ("mac", "box", "nightshift"):
        p = inst.run(f"python3 .datacore/lib/job_verify.py --machine {machine} --no-emit")
        out = p.stdout + p.stderr
        names = sorted(set(re.findall(r"job '([^']+)'", out)))
        checked = re.search(r"^OK (\d+) jobs", p.stdout, re.M)
        assert not names and checked and checked.group(1) == "0", (
            f"a stranger's install checks this fleet's jobs as {machine!r}: "
            f"{names or (checked.group(0) if checked else out[-300:])}")
