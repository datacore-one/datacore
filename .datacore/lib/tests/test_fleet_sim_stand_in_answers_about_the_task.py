"""The simulator's stand-in model gives its full answer every time it is asked.

Fleet simulation, 2026-11-19: the overnight run called the model more than once
for one task. The first call wrote the note the task asked for; a later call
found the file there, wrote nothing, and answered a bare "Done." The product's
output gate rightly called that report off-topic, so the delegated task ended
"for review" although its note existed. An honest stand-in answers about the
task whether it wrote the file in this call or found it already written.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
STAND_IN = LIB / "sim" / "stand_in.py"


def _ask(home: Path, prompt: str) -> str:
    claude = home / "bin" / "claude"
    claude.parent.mkdir(parents=True, exist_ok=True)
    if not claude.exists():
        claude.symlink_to(STAND_IN)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "SIM_STATE": str(home / "state"),
           "SIM_MACHINE": "m", "SIM_JOB": "j"}
    (home / "state").mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, str(claude), "-p"], input=prompt, env=env, cwd=str(home),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_a_second_call_for_the_same_task_still_answers_about_it(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    prompt = "## Task Body\nWrite the note to ~/Data/2-x/0-inbox/note.md\n"
    first = _ask(home, prompt)
    assert (home / "Data/2-x/0-inbox/note.md").is_file()
    second = _ask(home, prompt)
    assert len(first) > 60 and "note" in first
    assert second == first, f"the second call answered {second!r}"


def test_a_call_that_names_no_output_keeps_the_short_answer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert _ask(home, "Say hello.") == "Done."
