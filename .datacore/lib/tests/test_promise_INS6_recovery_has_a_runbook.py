"""INS-6: "Every known recovery situation has a written procedure that, followed
as written, returns the system to a healthy state."

Kind: deterministic.
  - The known recovery situations are the ones the catalogue names (OI-21 /
    audit C15): stale log, edit conflict, forked log, stranded run branch, bad
    event on origin, lost signing key, phase-1 rollback. For each, a tracked
    Markdown page carries a section whose heading names the situation and whose
    body gives the procedure as a command block.
  - Followed as written: for the stale-log situation (the one every writer can
    hit), a fixture space is rewound so the next append raises StaleLogError,
    the commands from that section are run in the fixture (placeholders
    <space>/$SPACE replaced by the fixture path), and afterwards the log
    verifies and accepts an append.

Seeded failure: recovery exists only in an error string (log.py's
StaleLogError text) or a docstring -- no page, so there is nothing to follow.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
DATA = LIB.parents[1]
sys.path.insert(0, str(LIB))

from ledger.log import EventLog, StaleLogError  # noqa: E402

SITUATIONS = {
    "stale log": r"stale\s+log|rewound\s+log|StaleLogError|seq(?:uence)?[- ]hwm",
    "edit conflict": r"edit\s+conflict|concurrent\s+edit",
    "forked log": r"fork(?:ed)?\s+(?:log|ledger)|ledger\s+fork",
    "stranded run branch": r"stranded\s+(?:run\s+)?branch",
    "bad event on origin": r"bad\s+event|void(?:ing)?\s+an?\s+event|bad\s+record",
    "lost signing key": r"(?:lost|rotat\w*)\s+(?:the\s+)?(?:signing\s+)?key|key\s+rotation",
    "phase-1 rollback": r"phase[- ]?1\s+(?:rollback|deactivat\w*)|roll\s*back\s+phase",
}


def _pages() -> list[Path]:
    out = subprocess.run(["git", "-C", str(DATA), "ls-files", "*.md"], capture_output=True,
                         text=True, timeout=30).stdout.splitlines()
    return [DATA / f for f in out if not f.startswith(("0-", "1-", "2-", "3-", "4-", "5-", "6-",
                                                        "7-", "8-", "9-", ".datacore/4-archive"))]


def _section(pattern: str) -> tuple[Path, str] | None:
    head = re.compile(r"^(#{1,6})\s+.*(?:" + pattern + r").*$", re.I | re.M)
    for p in _pages():
        try:
            text = p.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        m = head.search(text)
        if not m:
            continue
        level = len(m.group(1))
        nxt = re.compile(r"^#{1,%d}\s" % level, re.M).search(text, m.end())
        body = text[m.end(): nxt.start() if nxt else len(text)]
        if "```" in body:
            return p, body
    return None


def test_pages_are_searched():
    assert len(_pages()) > 20


@pytest.mark.parametrize("situation", sorted(SITUATIONS))
def test_each_recovery_situation_has_a_written_procedure(situation):
    found = _section(SITUATIONS[situation])
    assert found, (f"no tracked page has a '{situation}' section with a procedure to follow; "
                   "recovery that lives only in error strings cannot be followed")


def test_the_stale_log_procedure_followed_as_written_restores_health(tmp_path, monkeypatch):
    space = tmp_path / "space"
    log = EventLog(space, "fixture", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"t{i}", "title": f"task {i}"})
    f = next((space / ".datacore" / "events").glob("*.jsonl"))
    f.write_text("".join(f.read_text().splitlines(keepends=True)[:1]))   # a bad checkout rewinds it
    with pytest.raises(StaleLogError):
        EventLog(space, "fixture", sign=False).append("item.create", {"id": "t9", "title": "x"})

    found = _section(SITUATIONS["stale log"])
    assert found, "no written procedure for a stale log: nothing to follow"
    page, body = found
    blocks = re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", body, re.S)
    assert blocks, f"{page}: the stale-log section has no command block"
    env = dict(os.environ, DATACORE_ROOT=str(tmp_path), SPACE=str(space))
    for block in blocks:
        cmd = re.sub(r"<space[^>]*>|~/Data/<[^>]+>", str(space), block)
        p = subprocess.run(["bash", "-c", cmd], cwd=tmp_path, env=env, capture_output=True,
                           text=True, timeout=50)
        assert p.returncode == 0, f"{page}: step failed as written:\n{cmd}\n{p.stderr[-400:]}"
    EventLog(space, "fixture", sign=False).append("item.create", {"id": "t9", "title": "after"})
    v = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify", "--space", str(space)],
                       capture_output=True, text=True, timeout=50)
    assert v.returncode == 0, f"log does not verify after the procedure: {v.stdout + v.stderr}"
