"""MEM-19: Memory that loads into every session never holds customer names, amounts, people's
names, credentials or machine details.

Kind: production contract (the real auto-memory index ~/.claude/projects/-Users-gregor-Data/
memory/MEMORY.md and the `description:` frontmatter of every file it links -- both load into
every session's system prompt) + production contract on the write path (the PreToolUse hooks
really wired in ~/.claude/settings.json, probed with a Write of a forbidden index line).

Checked per line: customer names from the private customer denylist (read at runtime, never
printed), private/any IPv4 addresses, internal host names (*.local, *.internal, *.ts.net,
ssh user@host), credential-shaped tokens, and currency amounts. People's names cannot be
detected generically and are covered only where the denylist lists them.

Seeded failure: an index line "- [Deal](x.md) -- ACME pays EUR 40k, box at 10.0.0.12,
key sk-live-..." -- the line checker must flag it, and a Write of it into MEMORY.md must not
be silently allowed.
Red today (write path): no hook guards writes to MEMORY.md; the rule is prose at the top of
the file, kept by whoever edits it.
"""
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings_hooks  # noqa: E402

MEMORY_DIR = Path(os.environ.get(
    "CLAUDE_MEMORY_DIR", Path.home() / ".claude" / "projects" / "-Users-gregor-Data" / "memory"))
MEMORY = MEMORY_DIR / "MEMORY.md"
DENYLIST = Path.home() / ".datacore" / "private" / "customer-denylist.yaml"

GENERIC = [
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("internal host", re.compile(r"\b[\w-]+\.(?:local|internal|lan|ts\.net)\b", re.I)),
    ("ssh target", re.compile(r"\b[\w.-]+@[\w-]+(?:\.[\w-]+)*\b(?!\.(?:com|io|org|net|ai)\b)")),
    ("credential", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{10,}|xox[bp]-[\w-]{10,}"
                              r"|AKIA[0-9A-Z]{12,}|[A-Fa-f0-9]{40,}|eyJ[\w-]{20,})")),
    ("amount", re.compile(r"(?:[€$£]\s?\d[\d,.]*\s?[kKmM]?\b|\b\d[\d,.]*\s?[kKmM]?\s?(?:EUR|USD|GBP|CHF)\b"
                          r"|\b(?:EUR|USD|GBP|CHF)\s?\d[\d,.]*)")),
]


def _denylist_patterns():
    if not DENYLIST.is_file():
        return []
    data = yaml.safe_load(DENYLIST.read_text()) or {}
    out = []
    for p in data.get("forbidden_content") or []:
        try:
            out.append(("customer name (private denylist)", re.compile(p, re.I)))
        except re.error:
            continue
    return out


def offences(line: str, patterns) -> list[str]:
    return [label for label, rx in patterns if rx.search(line)]


def _loaded_lines() -> list[tuple[str, str]]:
    """(where, text) for everything that loads into every session."""
    out = []
    text = MEMORY.read_text(encoding="utf-8")
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("- "):
            out.append((f"MEMORY.md:{n}", line))
    for target in re.findall(r"\]\(<?([^)>]+\.md)>?\)", text):
        f = MEMORY_DIR / target
        if f.is_file():
            head = f.read_text(encoding="utf-8", errors="replace").split("\n---", 2)[0]
            for line in head.splitlines():
                if line.startswith("description:"):
                    out.append((f"{target}:description", line))
    return out


@pytest.mark.production
def test_loaded_memory_holds_no_forbidden_detail():
    assert MEMORY.is_file(), f"{MEMORY} missing"
    patterns = GENERIC + _denylist_patterns()
    bad = []
    for where, line in _loaded_lines():
        hit = offences(line, patterns)
        if hit:
            bad.append(f"{where}: {', '.join(sorted(set(hit)))}")   # never print the line itself
    assert not bad, "always-loaded memory holds forbidden detail:\n" + "\n".join(bad)


SEEDED = ("- [Deal](deal.md) -- ACME Holdings pays EUR 40k on 2026-10-01, runs on box 10.0.0.12, "
          "key sk-live-abcdef1234567890")


def test_checker_catches_a_seeded_line():
    """Guards against a vacuous green: the checker above must see a planted violation."""
    hit = set(offences(SEEDED, GENERIC))
    assert {"ipv4", "credential", "amount"} <= hit, hit


@pytest.mark.production
def test_writing_a_forbidden_index_line_is_not_silently_allowed():
    new = MEMORY.read_text(encoding="utf-8") + "\n" + SEEDED + "\n"
    out = settings_hooks.probe("PreToolUse", {"tool_input": {"file_path": str(MEMORY), "content": new}},
                               tool="Write")
    assert out.decision != "allow" or re.search(r"MEMORY|disclos|forbidden", out.context, re.I), (
        "a Write of an index line naming a customer, an amount, an IP and a key into MEMORY.md is "
        f"allowed silently -- no wired hook guards the always-loaded memory (ran: {len(out.ran)} hooks)")
