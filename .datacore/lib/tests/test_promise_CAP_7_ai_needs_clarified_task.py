"""Promise CAP-7 (catalogue GT-05):

    Nothing captured automatically is handed to AI workers until it has been
    clarified into a task with a clear definition of done.

Kind: deterministic, against a tmp Data tree.
  1. Automatic capture never produces work for the AI queue: GitHub triage,
     mail triage and tab capture write no :AI: tag.
  2. The executor's selection (`task_queue.find_unqueued_ai_tasks`,
     executable_only=True — the overflow build_queue uses on the run path)
     picks a clarified :AI: task with SURFACE and DONE_WHEN, and refuses one
     without a definition of done.
  3. An :AI: item still sitting in an inbox is not clarified (clarifying is
     what moves it out of the inbox) and is not selected, even if it carries
     the properties.
  4. The commit gate (`ai_task_gate.py`) refuses an :AI: task without
     DONE_WHEN.

Seeded failure: GitHub triage tags its capture :AI: again (the pre-dabc007
state), or selection runs with executable_only=False.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
DC = LIB.parent
NS_LIB = DC / "modules" / "nightshift" / "lib"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _task(title: str, props: dict, tags: str = ":AI:") -> str:
    lines = [f"** TODO {title} {tags}", ":PROPERTIES:"]
    lines += [f":{k}: {v}" for k, v in props.items()]
    return "\n".join(lines + [":END:"]) + "\n"


def _tree(tmp_path: Path) -> Path:
    data = tmp_path / "Data"
    org = data / "0-personal" / "org"
    org.mkdir(parents=True)
    spec = {"SURFACE": "datacore", "DONE_WHEN": "the report exists at docs/r.md"}
    (org / "next_actions.org").write_text(
        "#+TITLE: Next Actions\n\n* Work\n"
        + _task("Clarified agent task", {"ID": "na-ok", **spec})
        + _task("Agent task with no definition of done", {"ID": "na-nodone", "SURFACE": "datacore"}),
        encoding="utf-8")
    (org / "inbox.org").write_text(
        "#+TITLE: Inbox\n\n* Inbox\n" + _task("Auto-captured item in the inbox", {"ID": "in-1", **spec}),
        encoding="utf-8")
    return data


def _selected(data: Path) -> set[str]:
    if str(NS_LIB) not in sys.path:
        sys.path.insert(0, str(NS_LIB))
    import task_queue
    return {t.title.strip() for t in task_queue.find_unqueued_ai_tasks(data, None, None, executable_only=True)}


def test_ai_workers_get_only_clarified_tasks_with_a_definition_of_done(tmp_path):
    picked = _selected(_tree(tmp_path))
    assert "Clarified agent task" in picked, f"control: a clarified task must be selectable ({picked})"
    assert "Agent task with no definition of done" not in picked


def test_an_unclarified_inbox_capture_is_not_handed_to_ai_workers(tmp_path):
    picked = _selected(_tree(tmp_path))
    assert "Auto-captured item in the inbox" not in picked, \
        "an :AI: item still in the inbox (not clarified) was selected for an AI worker"


def test_automatic_capture_writes_no_ai_tag(tmp_path):
    data = tmp_path / "Data"
    org = data / "5-team" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text("#+TITLE: Inbox\n\n* Inbox\n", encoding="utf-8")
    (org / "next_actions.org").write_text("* Work\n", encoding="utf-8")
    gh = _load("gh_task_creator_cap7", DC / "modules" / "github" / "lib" / "task_creator.py")
    gh.create_tasks_from_scan({"mentions": [{"repo": "team-org/w", "number": 3, "title": "t",
                                             "url": "https://github.com/team-org/w/issues/3"}]},
                              data, {"team-org": ["5-team"]})
    host = _load("tab_host_cap7", DC / "modules" / "tab-capture" / "lib" / "host.py")
    host.capture_tabs([{"title": "x", "url": "https://example.org/x"}],
                      {"inbox_path": str(org / "inbox.org"), "filtered_prefixes": []})
    mail = _load("mail_task_creator_cap7", DC / "modules" / "mail" / "lib" / "task_creator.py")
    mail.create_tasks_from_scan({"categories": {"actionable": [{"id": "abcdef123456", "sender": "a@b.example",
                                                                "subject": "s"}]}},
                                data, target_org=str(org / "inbox.org"))
    text = (org / "inbox.org").read_text(encoding="utf-8")
    assert text.count("** TODO") >= 2, text
    heads = [l for l in text.splitlines() if l.startswith("*")]
    assert not [h for h in heads if ":AI" in h], f"an automatic capture was tagged for the AI queue: {heads}"


def test_the_commit_gate_refuses_an_ai_task_without_done_when(tmp_path):
    data = _tree(tmp_path)
    bad = data / "0-personal" / "org" / "next_actions.org"
    r = subprocess.run([sys.executable, str(LIB / "ai_task_gate.py"), str(bad)],
                       capture_output=True, text=True, timeout=60, cwd=str(LIB))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no definition of done" in (r.stdout + r.stderr) or "DONE_WHEN" in (r.stdout + r.stderr)
