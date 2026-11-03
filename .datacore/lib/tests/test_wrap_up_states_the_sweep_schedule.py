"""wrap-up.yaml states the learning sweep's real schedule (plan 2026-10-31, phase H, memory 2).

The workflow said the sweep runs at 05:20 from launchd. That job
(mac-session-learning, io.datacore.session-learning) was deleted on 2026-10-05
and nothing in the manifest replaced it. A command spec that names a schedule
the manifest does not hold tells the reader the archive is being drained when it
is not.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

DC = Path(__file__).resolve().parents[2]
WRAP_UP = DC / "workflows" / "wrap-up.yaml"
MANIFEST = DC / "lib" / "jobs" / "manifest.yaml"


def _sweep_jobs() -> list[dict]:
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return [j for j in doc.get("jobs") or [] if "session_learning" in str(j.get("cmd", ""))]


def test_every_time_wrap_up_names_for_the_sweep_is_a_manifest_schedule():
    text = WRAP_UP.read_text(encoding="utf-8")
    times = re.findall(r"\b(\d{1,2}):(\d{2})\b", text)
    scheduled = {(str(int(s.split()[1])), s.split()[0].zfill(2))
                 for s in (str(j.get("schedule", "")) for j in _sweep_jobs()) if len(s.split()) >= 2}
    stray = [f"{h}:{m}" for h, m in times if (str(int(h)), m) not in scheduled]
    assert not stray, f"wrap-up.yaml names {stray} for the sweep; the manifest schedules {scheduled or 'nothing'}"


def test_with_no_scheduled_sweep_wrap_up_says_so_and_how_to_run_it():
    if _sweep_jobs():
        return
    text = WRAP_UP.read_text(encoding="utf-8")
    assert "launchd" not in text, "the launchd job was retired on 2026-10-05"
    assert "no schedule" in text.lower()
    assert "session_learning_sweep.py --backlog" in text
