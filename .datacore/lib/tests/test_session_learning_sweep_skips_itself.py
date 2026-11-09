"""The learning sweep never sweeps its own runs (plan 2026-10-31, phase H, memory 1).

The sweep's `claude -p` sessions are archived like any other and come back as
`pending`. Mining them re-mines last night's extraction instead of real work
(memory note ENG-2026-08-20-024: the recursion reached fifth order). A session
whose first prompt is the sweep's own prompt is skipped, and the guard matches
what the sweep's prompt template actually emits.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session_learning_sweep as sweep  # noqa: E402

DAY = "2026-10-31"


def _meta(root: Path, sid: str, first_prompt: str) -> None:
    d = root / DAY / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({
        "session_id": sid, "learning_status": "pending", "first_prompt": first_prompt,
        "turns": 60, "output_tokens": 30_000}))


def test_a_sweep_run_is_skipped_and_a_work_session_is_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "ARCHIVE_DIR", tmp_path)
    own = sweep.build_prompt(DAY, [{"session_id": "w1", "first_prompt": "fix the hub",
                                    "_meta_path": str(tmp_path / "x" / "meta.json")}])
    _meta(tmp_path, "self1", own)
    _meta(tmp_path, "work1", "Fix the hub's seal age check")
    to_sweep, to_skip = sweep.pending_sessions(DAY)
    assert [m["session_id"] for m in to_sweep] == ["work1"]
    assert [m["session_id"] for m in to_skip] == ["self1"]
    assert "self-sweep" in to_skip[0]["_skip_reason"]


def test_the_guard_matches_the_prompt_the_sweep_sends():
    assert sweep.build_prompt(DAY, []).startswith(sweep.SELF_SWEEP_PREFIX)
    assert sweep.SELF_SWEEP_PREFIX == "Extract durable learning from"
