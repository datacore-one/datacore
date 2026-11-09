"""Command step tracker: every numbered step of a multi-step command is tracked
in the day's journal, independently of the harness.

DONE_WHEN: "/today and /wrap-up track every numbered step through a Datacore
step tracker that works from a shell or the MCP in any harness: the run's
checklist (every step, `- [ ]`) is written into the day's journal at start,
each step is ticked as it completes with a timestamp, an unfinished run is
visible (unticked steps) and can be resumed; TaskCreate, when present, only
mirrors it. Checked by tests."

Owner decision 2026-09-28: TaskCreate/TodoWrite are session-local UI
checklists that some sessions do not offer at all, so they may only mirror
the tracker, never be the mechanism.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
DATA = LIB.parents[1]
SCRIPT = LIB / "command_steps.py"
TODAY_MD = DATA / ".datacore" / "commands" / "today.md"
WRAP_UP_MD = DATA / ".datacore" / "commands" / "wrap-up.md"

import command_steps as cs  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A throwaway install: one personal space, found by its bare name."""
    r = tmp_path / "Data"
    space = r / "0-personal"
    (space / ".datacore").mkdir(parents=True)
    (space / ".datacore" / "config.yaml").write_text("space:\n  name: personal\n  type: personal\n")
    (space / "notes" / "journals").mkdir(parents=True)
    cmds = r / ".datacore" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "today.md").write_text(TODAY_MD.read_text())
    (cmds / "wrap-up.md").write_text(WRAP_UP_MD.read_text())
    monkeypatch.setenv("DATACORE_ROOT", str(r))
    return r


def _journal(root: Path, date: str) -> Path:
    return root / "0-personal" / "notes" / "journals" / f"{date}.md"


def _cli(root: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    # A harness-free environment: no Claude Code variables at all.
    env = {k: v for k, v in os.environ.items() if "CLAUDE" not in k.upper()}
    env["DATACORE_ROOT"] = str(root)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=60)


# --- parsing the real command files -------------------------------------------------

def test_parses_every_numbered_step_of_the_real_today_command():
    steps = cs.parse_steps(TODAY_MD.read_text())
    ids = [s["id"] for s in steps]
    headings = re.findall(r"^## Step (\d+[a-z]?):", _unfenced(TODAY_MD.read_text()), re.M)
    assert ids == headings, "every '## Step N:' heading is one tracked step, in order"
    assert ids[0] == "1" and "8b" in ids and "8c" in ids
    assert all(s["title"] for s in steps)


def test_parses_the_twelve_steps_of_the_real_wrap_up_command():
    import wrap_up_report
    steps = cs.parse_steps(WRAP_UP_MD.read_text())
    assert [s["id"] for s in steps] == [str(i) for i in range(1, wrap_up_report.CHECKLIST_STEPS + 1)]
    assert steps[0]["title"].startswith("Pulse")
    assert not any(s["id"].startswith("0") for s in steps), "the 0a-0f preamble is not a step"


def _unfenced(text: str) -> str:
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


# --- start / tick / status / resume -------------------------------------------------

def test_start_writes_the_full_checklist_into_the_days_journal(root):
    run = cs.start("today", date="2026-09-28")
    journal = _journal(root, "2026-09-28")
    text = journal.read_text()
    steps = cs.parse_steps(TODAY_MD.read_text())
    assert run["journal"] == str(journal)
    assert run["run_id"] in text
    assert text.startswith("---\ndate: 2026-09-28\n"), "a new journal gets the daily frontmatter"
    assert text.count("\n- [ ] ") == len(steps)
    for s in steps:
        assert f"- [ ] {s['id']}. {s['title']}" in text


def test_start_preserves_existing_journal_content_and_is_idempotent_per_run(root):
    journal = _journal(root, "2026-09-28")
    journal.write_text("---\ndate: 2026-09-28\ntype: daily\n---\n\n## Earlier\n\nKept.\n")
    first = cs.start("wrap-up", date="2026-09-28", run_id="wrap-up-20260928-090000-abcd")
    again = cs.start("wrap-up", date="2026-09-28", run_id="wrap-up-20260928-090000-abcd")
    text = journal.read_text()
    assert "## Earlier\n\nKept.\n" in text
    assert text.count("<!-- command-steps run=wrap-up-20260928-090000-abcd ") == 1, \
        "same run id, one block"
    assert first["run_id"] == again["run_id"]


def test_tick_marks_exactly_one_step_with_a_timestamp(root):
    run = cs.start("wrap-up", date="2026-09-28")
    status = cs.tick(run["run_id"], ["3"])
    lines = [ln for ln in _journal(root, "2026-09-28").read_text().splitlines() if ln.startswith("- [")]
    ticked = [ln for ln in lines if ln.startswith("- [x]")]
    assert len(ticked) == 1 and ticked[0].startswith("- [x] 3. ")
    assert re.search(r" — done \d\d:\d\d:\d\d$", ticked[0])
    assert status["done"] == ["3"]
    assert "3" not in status["pending"] and len(status["pending"]) == 11
    assert status["complete"] is False


def test_tick_rejects_an_unknown_step(root):
    run = cs.start("wrap-up", date="2026-09-28")
    with pytest.raises(cs.StepError):
        cs.tick(run["run_id"], ["99"])


def test_tick_note_becomes_the_wrap_up_checklist_status(root):
    run = cs.start("wrap-up", date="2026-09-28")
    cs.tick(run["run_id"], ["1"], note="not-answered")
    cs.tick(run["run_id"], ["2"])
    rows = cs.status(run["run_id"])["checklist"]
    assert len(rows) == 12
    assert rows[0] == {"step": "1", "title": rows[0]["title"], "status": "not-answered"}
    assert rows[1]["status"] == "run ✓"
    assert rows[2]["status"] == "not run"


def test_resume_finds_the_unfinished_run_and_forgets_a_finished_one(root):
    run = cs.start("wrap-up", date="2026-09-28")
    cs.tick(run["run_id"], ["1", "2"])
    found = cs.resume("wrap-up", date="2026-09-28")
    assert found is not None and found["run_id"] == run["run_id"]
    assert found["pending"][0] == "3"
    cs.tick(run["run_id"], [str(i) for i in range(3, 13)])
    assert cs.status(run["run_id"])["complete"] is True
    assert cs.resume("wrap-up", date="2026-09-28") is None


# --- harness independence ----------------------------------------------------------

def test_the_tracker_never_depends_on_a_harness_checklist_tool():
    source = SCRIPT.read_text()
    for tool in ("TaskCreate", "TaskUpdate", "TaskList", "TodoWrite"):
        assert tool not in source


def test_cli_round_trip_runs_from_a_plain_shell(root):
    out = _cli(root, "start", "today", "--date", "2026-09-28")
    assert out.returncode == 0, out.stderr
    run = json.loads(out.stdout)
    tick = _cli(root, "tick", run["run_id"], "1")
    assert tick.returncode == 0, tick.stderr
    status = json.loads(_cli(root, "status", run["run_id"]).stdout)
    assert status["done"] == ["1"]
    resumed = _cli(root, "resume", "today", "--date", "2026-09-28")
    assert json.loads(resumed.stdout)["run_id"] == run["run_id"]


def test_the_command_files_drive_the_tracker_and_keep_task_tools_optional():
    for path in (TODAY_MD, WRAP_UP_MD, DATA / ".datacore" / "agents" / "wrap-up-executor.md",
                 DATA / ".datacore" / "agents" / "ai-task-executor.md"):
        text = path.read_text()
        assert "command_steps.py" in text, f"{path.name} does not use the step tracker"
        body = text.split("\n---\n", 1)[1] if text.startswith("---\n") else text
        for line in body.splitlines():
            if re.search(r"TaskCreate|TaskList|TaskUpdate|TodoWrite", line):
                assert re.search(r"mirror|optional", line, re.I), \
                    f"{path.name}: a checklist tool is required, not optional: {line.strip()[:120]}"


# --- safe journal writes ------------------------------------------------------------

def test_journal_writes_use_the_shared_journal_transaction(root, monkeypatch):
    import journal_store
    calls = []
    real = journal_store.update_journal

    def spy(path, modifier):
        calls.append(Path(path))
        return real(path, modifier)
    monkeypatch.setattr(journal_store, "update_journal", spy)
    run = cs.start("wrap-up", date="2026-09-28")
    cs.tick(run["run_id"], ["1"])
    assert calls == [_journal(root, "2026-09-28")] * 2


def test_concurrent_ticks_and_journal_writers_lose_nothing(root, tmp_path):
    run = cs.start("wrap-up", date="2026-09-28")
    journal = _journal(root, "2026-09-28")
    state = {"DATACORE_STATE": os.environ["DATACORE_STATE"]}
    env = {k: v for k, v in os.environ.items() if "CLAUDE" not in k.upper()}
    env.update(state, DATACORE_ROOT=str(root), PYTHONPATH=str(LIB))
    writer = ("import sys, journal_store; from pathlib import Path; "
              "journal_store.update_journal(Path(sys.argv[1]), "
              "lambda t: t + '\\n## Other writer ' + sys.argv[2] + '\\n')")
    procs = [subprocess.Popen([sys.executable, str(SCRIPT), "tick", run["run_id"], str(i)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for i in range(1, 13)]
    procs += [subprocess.Popen([sys.executable, "-c", writer, str(journal), str(i)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
              for i in range(3)]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    text = journal.read_text()
    assert all(f"## Other writer {i}" in text for i in range(3))
    assert cs.status(run["run_id"])["complete"] is True
