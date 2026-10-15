"""The Stop hook refuses a medical-analysis reply that has no Verification status block.

A turn is a medical analysis when, since the last human prompt, a tool call touched a
person's record (a path under health-people/ or the records database). Owner, 2026-10-15.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "medical_review_gate.py"


def _line(kind, content):
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}})


def _transcript(tmp_path, touched, reply, earlier_turn_touched=False):
    lines = []
    if earlier_turn_touched:
        lines += [
            _line("user", "earlier question"),
            _line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "sqlite3 0-personal/1-active/health-people/x/data/records/records.db 'select 1'"}}]),
            _line("assistant", [{"type": "text", "text": "earlier answer\n\nVerification status — reviewer: run (1 claims: 1 confirmed, 0 corrected, 0 unverified)"}]),
        ]
    lines.append(_line("user", "how is the bloodwork"))
    if touched:
        lines.append(_line("assistant", [{"type": "tool_use", "name": "Read", "input": {"file_path": "0-personal/1-active/health-people/x/data/documents/a.pdf"}}]))
        lines.append(_line("user", [{"type": "tool_result", "content": "pdf text"}]))
    else:
        lines.append(_line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "ls 9-practice"}}]))
        lines.append(_line("user", [{"type": "tool_result", "content": "org"}]))
    lines.append(_line("assistant", [{"type": "text", "text": reply}]))
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def _run(transcript, stop_hook_active=False):
    payload = json.dumps({"transcript_path": str(transcript), "stop_hook_active": stop_hook_active})
    out = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else {}


def test_blocks_analysis_without_status(tmp_path):
    t = _transcript(tmp_path, touched=True, reply="Haemoglobin is 126, below range.")
    r = _run(t)
    assert r.get("decision") == "block"
    assert "Verification status" in r.get("reason", "")


def test_allows_analysis_with_status(tmp_path):
    t = _transcript(tmp_path, touched=True, reply="Haemoglobin is 126.\n\nVerification status — reviewer: run (3 claims: 3 confirmed, 0 corrected, 0 unverified) · last row checked: 2026-09-14 · open: none")
    assert _run(t) == {}


def test_allows_turn_that_did_not_touch_records(tmp_path):
    t = _transcript(tmp_path, touched=False, reply="The Practice has five tracks.")
    assert _run(t) == {}


def test_only_the_current_turn_counts(tmp_path):
    t = _transcript(tmp_path, touched=False, reply="Nothing medical here.", earlier_turn_touched=True)
    assert _run(t) == {}


def test_never_loops(tmp_path):
    t = _transcript(tmp_path, touched=True, reply="Haemoglobin is 126.")
    assert _run(t, stop_hook_active=True) == {}


def test_missing_transcript_is_silent(tmp_path):
    r = _run(tmp_path / "missing.jsonl")
    assert r == {}
