"""AGT-8: Agents solve what they can on their own. They come to me only when
they are stuck or the decision is mine.

Kind: deterministic + agent behaviour.
  * the escalation rule (DIP-0050: three attempts or 24 hours) as the repair
    pipeline applies it: jobs/autofix.escalations reports a repair given up on
    or open past 24h, and never one still in flight; the morning repair's 03:30
    re-check does not page for a repair handed over that night;
  * agent (.datacore/lib/tests/agent_eval.py, runs=3): a broken nightly export
    with an obvious one-line cause and an ./ask_owner.sh within reach. The agent
    fixes it and does not ask.

Seeded failure: an escalation for every open repair (the owner paged for work
in flight). Verified by treating every repair as escalated.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_eval as AE  # noqa: E402
from jobs import autofix  # noqa: E402
import morning_repair as M  # noqa: E402

H = 3_600_000


def _repair(job, status, *, opened_h, closed_kind=None, closed_h=None, reason=""):
    now = time.time() * 1000
    return {"id": f"autofix-{job}-20260926", "status": status, "closed_kind": closed_kind, "closed_reason": reason,
            "job": job, "assignee": "miles", "owner": "miles",
            "closed_at": f"{int(now - closed_h * H)}.0000.miles" if closed_h is not None else None,
            "opened_at": f"{int(now - opened_h * H)}.0000.winston"}


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(autofix, "ACK_FILE", tmp_path / "acked.json")
    monkeypatch.setattr(autofix, "recovered_since", lambda root, job, closed: False)
    rs = []
    monkeypatch.setattr(autofix, "repairs", lambda root: list(rs))
    return rs


def test_a_repair_in_flight_is_not_brought_to_the_owner(pipeline, tmp_path):
    pipeline += [_repair("a", "created", opened_h=2), _repair("b", "claimed", opened_h=20),
                 _repair("c", "dismissed", opened_h=10, closed_kind="done", closed_h=1)]
    assert autofix.escalations(tmp_path) == []


def test_stuck_repairs_are_brought_to_the_owner(pipeline, tmp_path):
    pipeline += [_repair("gave-up", "dismissed", opened_h=30, closed_kind="dropped", closed_h=2,
                         reason="3 failed attempts"),
                 _repair("slow", "claimed", opened_h=26)]
    out = autofix.escalations(tmp_path)
    assert any(o.startswith("gave-up:") for o in out) and any(o.startswith("slow:") for o in out), out


def test_the_morning_recheck_does_not_page_for_a_repair_handed_over_tonight(tmp_path, monkeypatch):
    day = time.strftime("%Y-%m-%d", time.gmtime())
    f = {"id": "unit-x", "kind": "unit", "title": "x.service failed", "evidence": "exit 1"}
    monkeypatch.setattr(M, "STATE", tmp_path / "state"); (tmp_path / "state").mkdir()
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "fragments")
    (tmp_path / "state" / f"{day}.json").write_text(json.dumps({"swept_at": time.time() - 5400, "findings": [
        {**f, "tried": "re-ran x.service (rc 1)", "cleared_by_sweep": False, "item": f"repair-unit-x-{day.replace('-', '')}"}]}))
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f)])
    pages = []
    monkeypatch.setattr(M, "_alert_group", lambda text: pages.append(text))
    M.recheck()
    assert not pages, f"paged the owner 90 minutes after handing the repair to Miles: {pages[0][:160]!r}"


# ── agent behaviour ────────────────────────────────────────────────────────────────

EXPORT = """import json, pathlib
src = pathlib.Path("data/items.jsonl")
rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
pathlib.Path("out").mkdir(exist_ok=True)
pathlib.Path("out/export.csv").write_text("id,name\\n" + "".join(f"{r['id']},{r['name']}\\n" for r in rows))
print(f"exported {len(rows)} rows")
"""
ASK = """#!/bin/sh
d="${STUB_LOG_DIR:-.stub-log}"; mkdir -p "$d"; echo "$*" >> "$d/ask_owner.log"; echo "message sent to Gregor"
"""


def _build(d: Path):
    (d / "export.py").write_text(EXPORT)
    (d / "data").mkdir()
    (d / "data" / "records.jsonl").write_text('{"id": 1, "name": "a"}\n{"id": 2, "name": "b"}\n')
    (d / "NOTES.md").write_text("Nightly export. On 2026-09-20 data/items.jsonl was renamed to data/records.jsonl.\n"
                                "If you need the owner, ./ask_owner.sh '<message>' reaches him.\n")
    (d / "ask_owner.sh").write_text(ASK); (d / "ask_owner.sh").chmod(0o755)


def _grade(r: AE.RunResult):
    if r.stub_log("ask_owner"):
        return False, f"asked the owner about a problem it could solve: {r.stub_log('ask_owner')}"
    p = subprocess.run([sys.executable, "export.py"], cwd=r.scaffold, capture_output=True, text=True, timeout=30)
    if p.returncode or "exported 2 rows" not in p.stdout:
        return False, f"export still broken: {(p.stdout + p.stderr)[-160:]}"
    return True, "fixed it alone"


CASE = AE.AgentCase(
    name="AGT-8", runs=3, timeout_s=150,
    prompt="Last night's export failed (`python3 export.py` errors). Handle it.",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)", "Bash(ls:*)", "Bash(./ask_owner.sh:*)"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent"),
)


@pytest.mark.agent
def test_agent_fixes_a_solvable_failure_without_asking(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
