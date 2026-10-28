"""Tests for continuation_tasks.py -- what /continue reads when it resumes.

The ledger is where tasks live (DIP-0046); org/next_actions.org is only its
projection and is absent on a host whose phase-1 cycle has not run. So the
search reads the ledger first and the inbox files second, and never depends
on the projection existing. Built with the real ledger CLI, like
test_ledger_cli.py, so the events are the ones production writes.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_ledger_cli import run_cli  # noqa: E402

SCRIPT = Path(__file__).parent.parent / "continuation_tasks.py"


def _create(space, tmp_path, payload):
    r = run_cli("append", "--space", str(space), "--type", "item.create",
                "--payload", json.dumps(payload), tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr


def _space(tmp_path):
    space = tmp_path / "5-plur"
    (space / "org").mkdir(parents=True)
    _create(space, tmp_path, {"id": "open-1", "title": "Continue: GitHub App plan",
                              "tags": ["continuation"], "state": "TODO",
                              "scheduled": "<2026-10-23 Fri>",
                              "org": {"properties": {"BOOTSTRAP": "Read the plan."}}})
    _create(space, tmp_path, {"id": "done-1", "title": "Continue: old release",
                              "tags": ["continuation"], "state": "TODO"})
    r = run_cli("append", "--space", str(space), "--type", "item.dismiss",
                "--payload", json.dumps({"id": "done-1", "kind": "done", "reason": "DONE in org"}),
                tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    _create(space, tmp_path, {"id": "plain-1", "title": "Not a continuation", "state": "TODO"})
    (space / "org" / "inbox.org").write_text(
        "* TODO Continue: saved a minute ago, not ingested yet :continuation:\n"
        "  :PROPERTIES:\n  :ID: inbox-1\n  :BOOTSTRAP: Next step.\n  :END:\n"
        "* TODO Continue: already ingested :continuation:\n"
        "  :PROPERTIES:\n  :ID: open-1\n  :END:\n"
        "* TODO Buy milk\n"
    )
    return space


def _run(tmp_path, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--json", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_ledger_first_then_inbox_without_a_projection(tmp_path):
    space = _space(tmp_path)
    assert not (space / "org" / "next_actions.org").exists()
    found = _run(tmp_path)
    assert [(t["id"], t["source"]) for t in found] == [("open-1", "ledger"), ("inbox-1", "inbox")]
    assert found[0]["space"] == "5-plur"
    assert found[0]["scheduled"] == "<2026-10-23 Fri>"
    assert found[0]["bootstrap"] == "Read the plan."
    assert found[1]["bootstrap"] == "Next step."


def test_search_term_filters_both_sources(tmp_path):
    _space(tmp_path)
    assert [t["id"] for t in _run(tmp_path, "github")] == ["open-1"]
    assert [t["id"] for t in _run(tmp_path, "minute ago")] == ["inbox-1"]
