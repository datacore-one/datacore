"""One journal line per agent per day, written from the agent's own signed record.

Agents left no line in the shared journal from 2026-09-22 on; their work showed
only in commits and scoreboards. The line is built from the cadence-run end
events the agent already signs in each space's ledger, so it costs no model call
and states only what the record says.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))

import firm_journal_line as fjl  # noqa: E402

DAY = "2026-10-23"


def _ms(day: str, hour: int) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00").timestamp() * 1000)


def _end(actor, day, hour, slug, result, reason="ok", seq=1):
    return {"seq": seq, "hlc": f"{_ms(day, hour)}.0000.{actor}", "actor": actor, "type": "metric.attest",
            "payload": {"metric": "cadence.run", "phase": "end", "slug": slug, "result": result, "reason": reason},
            "prev": "", "hash": f"h{seq}{slug}", "sig": ""}


def _write_log(space: Path, actor: str, events):
    d = space / ".datacore" / "events"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{actor}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "Data"
    firm = r / "8-firm"
    (firm / "journal").mkdir(parents=True)
    ops = r / "6-ops"
    _write_log(ops, "agent-ops", [
        _end("agent-ops", DAY, 5, "cadence-ops-a", "ok", seq=1),
        _end("agent-ops", DAY, 6, "cadence-ops-b", "failed", "no file matching x was written", seq=2),
        _end("agent-ops", "2026-10-22", 6, "cadence-ops-old", "ok", seq=3),  # another day: not counted
        {**_end("agent-ops", DAY, 7, "cadence-ops-c", "ok", seq=4),
         "payload": {"metric": "cadence.run", "phase": "start", "slug": "cadence-ops-c"}},  # a start: not counted
    ])
    _write_log(firm, "agent-research", [_end("agent-research", DAY, 6, "cadence-firm-x", "ok", seq=1)])  # another agent
    return r


def test_line_counts_only_this_agents_ended_runs_of_the_day(root):
    line = fjl.day_line(root, "agent-ops", DAY)
    assert line.startswith("- **agent-ops** ")
    assert "2 cadence run(s) ended" in line
    assert "1 ok" in line and "1 failed" in line
    assert "cadence-ops-b" in line          # the one that was not ok is named
    assert "cadence-ops-old" not in line and "agent-research" not in line


def test_no_runs_is_said_plainly(root):
    assert fjl.day_line(root, "agent-comms", DAY) == "- **agent-comms** — no cadence run ended today (none recorded in the ledger)"


def test_write_is_idempotent_and_keeps_other_lines(root):
    firm = root / "8-firm"
    page = firm / "journal" / f"{DAY}.md"
    page.write_text(f"# {DAY}\n\n## Owner notes\n\nsomething the owner wrote\n")
    fjl.write_line(firm, DAY, fjl.day_line(root, "agent-research", DAY), "agent-research")
    fjl.write_line(firm, DAY, fjl.day_line(root, "agent-ops", DAY), "agent-ops")
    fjl.write_line(firm, DAY, fjl.day_line(root, "agent-ops", DAY), "agent-ops")  # a rerun replaces, never duplicates
    text = page.read_text()
    assert "something the owner wrote" in text
    assert text.count("- **agent-ops** ") == 1 and text.count("- **agent-research** ") == 1
    assert text.count(fjl.SECTION) == 1


def test_creates_the_days_page_when_missing(root):
    firm = root / "8-firm"
    fjl.write_line(firm, DAY, "- **agent-comms** — no cadence run ended today (none recorded in the ledger)", "agent-comms")
    text = (firm / "journal" / f"{DAY}.md").read_text()
    assert text.startswith(f"# {DAY}\n") and fjl.SECTION in text and "- **agent-comms** " in text


def test_cli_uses_the_principal_the_cadence_runner_sets(root):
    r = subprocess.run([sys.executable, str(LIB / "firm_journal_line.py"), "--date", DAY],
                       cwd=root / "8-firm", capture_output=True, text=True,
                       env={"DATACORE_ROOT": str(root), "DATACORE_POLICY_PRINCIPAL": "agent-ops", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert "- **agent-ops** — 2 cadence run(s) ended" in (root / "8-firm" / "journal" / f"{DAY}.md").read_text()


def test_template_declares_this_script_and_its_evidence():
    import yaml
    t = (LIB.parent / "templates" / "cadences" / "firm-journal-line.md").read_text()
    meta = yaml.safe_load(t.split("---\n", 2)[1])
    assert meta["script"] == ".datacore/lib/firm_journal_line.py"
    assert meta["evidence"]["path"] == "journal/{date}.md"
    assert meta["frequency"] == "daily"
