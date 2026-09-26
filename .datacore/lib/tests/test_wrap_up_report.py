"""The /wrap-up report is rendered, not composed — and stays the spec's template.

## What would have to break for these to fail

 - the renderer's section headers drift from commands/wrap-up.md §10 (or the
   spec's template changes without the renderer) → the lockstep test fails.
 - any layout change at all → the snapshot fails; updating it is a deliberate
   act, which is the point (Opus 5 → 5.5 changed the shape silently).
 - a missing required field renders a thinner report instead of refusing →
   the refusal tests fail.
 - a missing `meta` renders zeros or blanks instead of "unavailable" → fails.
 - `report --journal` appends twice, or `audit --final` passes without the
   report in the journal → the CLI tests fail.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import wrap_up_report as wr

LIB = Path(__file__).resolve().parents[1]
SPEC = LIB.parent / "commands" / "wrap-up.md"
SNAPSHOT = Path(__file__).parent / "fixtures" / "wrap_up_report.snapshot.txt"


def narrative(**over):
    n = {
        "goal": "Import health export and fix stale sprint blockers",
        "done": ["Imported export 7", "Merged enterprise #1576"],
        "decisions": ["Add a `dropped` state"],
        "next": "Continuation task 3aed2a24, Mon 2026-09-28",
        "continuation": [{"heading": "Continue sprint hygiene", "id": "3aed2a24",
                          "scheduled": "2026-09-28", "signals": "handoff prompt"}],
        "tasks_completed": {"auto": [], "suggested": ["org-x | claim.py auto-close"],
                            "retroactive": ["07c549e7 | Health import + sprint fix"]},
        "learnings": [],
        "engrams": ["ENG-2026-09-25-006"],
        "gtd_proposals": ["[#B] Close enterprise sprint 10 → Engineering"],
        "delegations": ["Close datacore-mcp#19"],
        "coverage": "Coverage: 6 insights — all in journal, 3 as tasks, 3 as PRs.",
        "meta": {"arc": "Import → Diagnosis → Fix", "observation": "Three causes, one symptom.",
                 "corrections": [{"error": "regex matched newlines", "category": "Judgment"}],
                 "user_role": "director"},
        "files": {"~/Data/": {"created": [".datacore/lib/sprint_files.py"],
                              "modified": [".datacore/lib/sprint_sync.py"]}},
        "artifacts": [{"type": "module", "path": ".datacore/lib/sprint_files.py",
                       "description": "Sprint discovery"}],
        "social": {"personal_x": "PRs merged in June were my blockers.",
                   "project_x": "Sprint files now checked against GitHub.",
                   "linkedin": "Hook line.\n\nBody.\n\nQuestion? #ai"},
        "pulse": None,
        "checklist": [{"step": i, "title": f"Step {i}", "status": "run ✓"} for i in range(1, 13)],
    }
    n.update(over)
    return n


MECH = {
    "meta": {"step": "meta", "turns": 358, "user_turns": 8, "tool_calls": 154,
             "agents_spawned": {"journal-coordinator": 1}, "output_tokens": 248762,
             "subagent_output_tokens": 5135, "billable_tokens": 1538025,
             "spaces_touched": ["5-plur", "root"]},
    "audit": {"checks": [
        {"check": "personal journal written", "pass": True,
         "detail": str(Path.home() / "Data/0-personal/notes/journals/2026-09-26.md")},
        {"check": "space journals", "pass": True,
         "detail": "2 written: ['5-plur/journal/2026-09-26.md', '9-practice/journal/2026-09-26.md']"},
        {"check": "session work committed and pushed", "pass": True, "detail": "3 repos"}]},
    "archive_meta": {"started_at": "2026-09-25T06:41:22Z", "ended_at": "2026-09-25T09:11:22Z",
                     "tokens": {"input_tokens": 722, "cache_creation_input_tokens": 1288541,
                                "cache_read_input_tokens": 79090006, "output_tokens": 248762}},
}


def _spec_template_headers() -> list[str]:
    text = SPEC.read_text()
    start = text.index("SESSION COMPLETE — CONSOLIDATED REPORT")
    end = text.index("Ready to close terminal.", start)
    block = text[start:end].splitlines()
    heads = []
    for i, line in enumerate(block):
        if i and block[i - 1] == wr.LIGHT and i + 1 < len(block) and block[i + 1] == wr.LIGHT:
            heads.append(line.strip())
    return heads


def test_section_headers_are_the_specs_in_order():
    spec = _spec_template_headers()
    assert spec, "could not find the §10 template in commands/wrap-up.md"
    assert spec == wr.SECTIONS


def test_rules_are_the_specs_characters():
    text = SPEC.read_text()
    assert wr.HEAVY in text and wr.LIGHT in text


def test_snapshot_is_byte_exact(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Ljubljana")
    import time
    time.tzset()
    out = wr.render(narrative(), MECH)
    if os.environ.get("UPDATE_SNAPSHOT"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(out)
    assert out == SNAPSHOT.read_text()


def test_every_section_renders_even_when_empty():
    n = narrative(continuation=[], files={}, artifacts=[], gtd_proposals=[], delegations=[],
                  decisions=[], engrams=[], tasks_completed={})
    out = wr.render(n, MECH)
    for h in wr.SECTIONS:
        assert f"{wr.LIGHT}\n{h}\n{wr.LIGHT}" in out
    assert out.startswith(wr.HEAVY + "\nSESSION COMPLETE — CONSOLIDATED REPORT\n" + wr.HEAVY)
    assert out.rstrip().endswith("Ready to close terminal.\n" + wr.HEAVY)


@pytest.mark.parametrize("drop", ["goal", "done", "next", "social", "checklist"])
def test_missing_required_field_refuses(drop):
    n = narrative()
    n.pop(drop)
    with pytest.raises(wr.ReportInputError, match=drop):
        wr.render(n, MECH)


def test_missing_social_draft_and_meta_observation_refuse():
    n = narrative(social={"personal_x": "x", "project_x": "y"})
    n["meta"] = {"arc": "a"}
    with pytest.raises(wr.ReportInputError) as e:
        wr.render(n, MECH)
    assert "social.linkedin" in str(e.value) and "meta.observation" in str(e.value)


def test_checklist_must_have_twelve_rows():
    with pytest.raises(wr.ReportInputError, match="12"):
        wr.render(narrative(checklist=[{"step": 1, "title": "x", "status": "run ✓"}]), MECH)


def test_missing_meta_is_unavailable_not_zero():
    """Other harnesses have no CLAUDE_CODE_SESSION_ID, so `meta` errors there."""
    mech = dict(MECH, meta={"step": "meta", "error": "session not archived yet"})
    mech.pop("archive_meta")
    out = wr.render(narrative(), mech)
    assert "Token cost unavailable" in out and "session not archived yet" in out
    assert "Session: timing unavailable" in out
    assert "Output tokens: 0" not in out


def test_session_crossing_midnight_shows_dates():
    mech = dict(MECH, archive_meta=dict(MECH["archive_meta"], ended_at="2026-09-26T09:35:02Z"))
    line = next(l for l in wr.render(narrative(), mech).splitlines() if l.startswith("Session:"))
    assert "2026-09-25" in line and "2026-09-26" in line


def test_checklist_count_counts_only_real_statuses():
    rows = [{"step": i, "title": "t", "status": "run ✓"} for i in range(1, 12)]
    rows.append({"step": 12, "title": "t", "status": "skipped"})
    assert "Checklist: 11/12 items verified" in wr.render(narrative(checklist=rows), MECH)


# ── CLI: report --journal and audit --final ────────────────────────────────

def _cli(tmp_path, *args, stdin=None):
    env = dict(os.environ, DATACORE_ROOT=str(tmp_path), DATACORE_SESSION_ID="test-sid")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable, str(LIB / "wrap_up_mechanics.py"), *args],
                          input=stdin, capture_output=True, text=True, env=env, cwd=tmp_path)


@pytest.fixture
def root(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)   # the Data root is always a repo
    (tmp_path / "0-personal" / "notes" / "journals").mkdir(parents=True)
    state = tmp_path / ".datacore" / "state" / "wrap_up" / "test-sid"
    state.mkdir(parents=True)
    (state / "meta.json").write_text(json.dumps(MECH["meta"]))
    (state / "audit.json").write_text(json.dumps(MECH["audit"]))
    return tmp_path


def test_report_cli_prints_template_and_journals_once(root):
    data = json.dumps(narrative())
    r = _cli(root, "report", "--input", "-", "--journal", stdin=data)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(wr.HEAVY)
    journals = list((root / "0-personal/notes/journals").glob("*.md"))
    assert len(journals) == 1
    text = journals[0].read_text()
    assert wr.MARKER.format(sid="test-sid") in text
    for sec in ("### Consolidated Report", "### Session Meta-Analysis", "### Token Cost",
                "## Wrap-up Checklist Audit"):
        assert sec in text
    again = _cli(root, "report", "--input", "-", "--journal", stdin=data)
    assert "not appended twice" in again.stderr
    assert journals[0].read_text().count(wr.MARKER.format(sid="test-sid")) == 1


def test_report_cli_refuses_incomplete_narrative(root):
    n = narrative()
    n.pop("social")
    r = _cli(root, "report", "--input", "-", stdin=json.dumps(n))
    assert r.returncode == 2 and "social" in r.stderr and r.stdout == ""


def test_audit_final_fails_until_report_is_in_journal(root):
    def final_checks():
        r = _cli(root, "audit", "--final")
        return {c["check"]: c["pass"] for c in json.loads(r.stdout)["checks"]}

    assert final_checks()["consolidated report in journal"] is False
    _cli(root, "report", "--input", "-", "--journal", stdin=json.dumps(narrative()))
    checks = final_checks()
    assert checks["consolidated report in journal"] is True
    assert checks["journal has 'Wrap-up Checklist Audit'"] is True
