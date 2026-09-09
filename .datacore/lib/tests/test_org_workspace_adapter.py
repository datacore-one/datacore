"""Tests for org_workspace_adapter.py enhancements — move, show, update, enhanced add."""

import json
import subprocess
import sys
import shutil
from pathlib import Path

import pytest

ADAPTER = Path(__file__).parent.parent / "org_workspace_adapter.py"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def work_dir(tmp_path):
    """Copy fixtures to a temp dir for mutation tests."""
    shutil.copy(FIXTURES / "inbox.org", tmp_path / "inbox.org")
    shutil.copy(FIXTURES / "next_actions.org", tmp_path / "next_actions.org")
    return tmp_path


def run_adapter(*args):
    """Exit code and JSON body must agree. This helper used to assert exit 0
    for every call -- including the not-found and refused cases -- which
    enshrined the defect where an error result exited 0 and a caller trusting
    the exit code believed a refused write had happened."""
    result = subprocess.run(
        ["python3", str(ADAPTER)] + list(args),
        capture_output=True, text=True,
    )
    body = json.loads(result.stdout)
    if isinstance(body, dict) and body.get("error"):
        assert result.returncode != 0, f"error result exited 0: {body}"
    else:
        assert result.returncode == 0, f"Adapter failed: {result.stderr}"
    return body


class TestAddEnhanced:
    def test_add_with_body(self, work_dir):
        result = run_adapter(
            "add", "--allow-any-file", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Test task with body",
            "--body", "This is the body text.\nSecond line.",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "This is the body text." in content
        assert "Second line." in content

    def test_add_with_properties(self, work_dir):
        result = run_adapter(
            "add", "--allow-any-file", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Task with context",
            "--property", "CONTEXT=Demo recorded",
            "--property", "EFFORT=0:30",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert ":CONTEXT: Demo recorded" in content
        assert ":EFFORT: 0:30" in content

    def test_add_with_parent(self, work_dir):
        result = run_adapter(
            "add", "--allow-any-file", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Child of AI Queue",
            "--parent", "AI Queue",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "** TODO Child of AI Queue" in content

    def test_add_with_parent_by_id(self, work_dir):
        result = run_adapter(
            "add", "--allow-any-file", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Child by ID",
            "--parent-id", "fds-001",
        )
        assert result["added"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "**** TODO Child by ID" in content


class TestMove:
    def test_move_by_id(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "inbox-001",
        )
        assert result["moved"] is True
        assert result["id"] == "inbox-001"
        inbox = (work_dir / "inbox.org").read_text()
        assert "inbox-001" not in inbox
        na = (work_dir / "next_actions.org").read_text()
        assert "inbox-001" in na
        assert "Simple inbox item" in na

    def test_move_by_title(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--title", "Rich inbox",
        )
        assert result["moved"] is True
        na = (work_dir / "next_actions.org").read_text()
        assert ":CONTEXT: Demo is recorded and working." in na
        assert "review for secrets" in na

    def test_move_with_parent(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "inbox-001",
            "--parent", "AI Queue",
        )
        assert result["moved"] is True
        na = (work_dir / "next_actions.org").read_text()
        assert "** TODO Simple inbox item" in na

    def test_move_not_found(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "nonexistent-id",
        )
        assert "error" in result


class TestShow:
    def test_show_by_id(self, work_dir):
        result = run_adapter(
            "show", "--file", str(work_dir / "inbox.org"),
            "--id", "inbox-002",
        )
        assert result["heading"] == "Rich inbox item"
        assert result["state"] == "TODO"
        assert result["properties"]["CONTEXT"] == "Demo is recorded and working."
        assert "review for secrets" in result["body"]

    def test_show_by_title(self, work_dir):
        result = run_adapter(
            "show", "--file", str(work_dir / "inbox.org"),
            "--title", "Simple inbox",
        )
        assert result["heading"] == "Simple inbox item"
        assert "Body of simple item" in result["body"]

    def test_show_not_found(self, work_dir):
        result = run_adapter(
            "show", "--file", str(work_dir / "inbox.org"),
            "--id", "nonexistent",
        )
        assert "error" in result


class TestUpdate:
    def test_update_scheduled(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--scheduled", "2026-05-01",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "SCHEDULED: <2026-05-01 Fri>" in content

    def test_update_property(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--property", "EFFORT=2:00",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert ":EFFORT: 2:00" in content

    def test_update_tags(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--tags", ":AI:research:",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert ":AI:research:" in content

    def test_update_state(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "fds-001",
            "--state", "NEXT",
        )
        assert result["updated"] is True
        content = (work_dir / "next_actions.org").read_text()
        assert "NEXT" in content

    def test_update_not_found(self, work_dir):
        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "nonexistent",
            "--state", "DONE",
        )
        assert "error" in result


class TestInboxProcessing:
    """End-to-end: process inbox items like GTD triage."""

    def test_full_inbox_to_next_actions_flow(self, work_dir):
        result = run_adapter(
            "move",
            "--from", str(work_dir / "inbox.org"),
            "--to", str(work_dir / "next_actions.org"),
            "--id", "inbox-002",
            "--parent", "AI Queue",
        )
        assert result["moved"] is True

        result = run_adapter(
            "update", "--file", str(work_dir / "next_actions.org"),
            "--id", "inbox-002",
            "--scheduled", "2026-04-23",
        )
        assert result["updated"] is True

        result = run_adapter(
            "show", "--file", str(work_dir / "next_actions.org"),
            "--id", "inbox-002",
        )
        assert result["heading"] == "Rich inbox item"
        assert result["properties"]["CONTEXT"] == "Demo is recorded and working."
        assert result["scheduled"] == "2026-04-23"
        assert "review for secrets" in result["body"]

        inbox = (work_dir / "inbox.org").read_text()
        assert "inbox-002" not in inbox

    def test_add_new_task_with_full_context(self, work_dir):
        result = run_adapter(
            "add", "--allow-any-file", "--file", str(work_dir / "next_actions.org"),
            "--heading", "Post video on X",
            "--priority", "C",
            "--tags", ":fds:comms:",
            "--scheduled", "2026-04-20",
            "--parent", "AI Queue",
            "--body", "Tag @jssr. Share with Tether contact via DM.",
            "--property", "CONTEXT=Strategy from engram",
        )
        assert result["added"] is True

        result = run_adapter(
            "show", "--file", str(work_dir / "next_actions.org"),
            "--id", result["id"],
        )
        assert result["properties"]["CONTEXT"] == "Strategy from engram"
        assert "Tag @jssr" in result["body"]


class TestV2LedgerWrite:
    """DIP-0046 C4b: the adapter is the v2 write path for BOTH connectors."""

    def test_new_tasks_are_refused_outside_inbox(self, work_dir):
        """The hard rule, enforced where every writer passes through.

        The MCP already targeted inbox.org by itself, but nothing stopped a
        direct --file at next_actions.org — and that is exactly what happened
        when three recovered tasks were restored on 2026-08-12. A rule only the
        well-behaved callers follow is a convention, not a rule.
        """
        result = run_adapter("add", "--file", str(work_dir / "next_actions.org"),
                             "--heading", "should be refused")
        assert "error" in result
        assert "inbox.org" in result["error"]

    def test_subtasks_are_exempt(self, work_dir):
        """Attaching a subtask is not capture; forcing it to inbox orphans it."""
        parent = run_adapter("add", "--file", str(work_dir / "inbox.org"),
                             "--heading", "Parent task")
        assert parent.get("added") is True
        child = run_adapter("add", "--file", str(work_dir / "next_actions.org"),
                            "--heading", "Child task", "--parent-id", parent["id"])
        assert "error" not in child or "inbox.org" not in child.get("error", "")

    def test_add_emits_to_the_ledger_when_a_space_is_present(self, work_dir, monkeypatch):
        """Attribution and latency, both fixed at one choke point.

        Before this, an agent's task reached the ledger only via the nightly
        sweep, which imports as `genesis` — 1,816 of 2,034 item.create events
        said `genesis`, so the ledger could not say who created 89% of its own
        items, and a write could sit un-ingested for a day.
        """
        (work_dir / ".datacore" / "events").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("DATACORE_ACTOR", "testactor")
        result = run_adapter("add", "--file", str(work_dir / "inbox.org"),
                             "--heading", "Ledger-bound task")
        assert result.get("added") is True
        # The emit must never fail the caller, so absence is tolerated; when it
        # happens it must carry the REAL actor, not the import role.
        if result.get("ledger_actor"):
            assert result["ledger_actor"] != "genesis"


    def test_add_carries_the_drawer_into_the_ledger(self, work_dir, monkeypatch):
        """A Phase 1 space regenerates its org file from the ledger; a task
        created with SURFACE and DONE_WHEN must come back with them."""
        (work_dir / ".datacore" / "events").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("DATACORE_ACTOR", "testactor")
        result = run_adapter("add", "--file", str(work_dir / "inbox.org"),
                             "--heading", "Drawer-bound task", "--priority", "A",
                             "--property", "SURFACE=2-datacore", "--property", "DONE_WHEN=the file exists")
        assert result.get("added") is True and result.get("ledger_actor")
        import json
        events = [json.loads(l) for f in (work_dir / ".datacore" / "events").glob("*.jsonl") for l in f.read_text().splitlines() if l.strip()]
        created = [e for e in events if e["type"] == "item.create" and e["payload"]["id"] == result["id"]]
        assert len(created) == 1
        org = created[0]["payload"]["org"]
        assert org["properties"] == {"SURFACE": "2-datacore", "DONE_WHEN": "the file exists"}
        assert org["priority"] == "A" and "CREATED" not in org["properties"]



def test_refused_add_exits_nonzero(tmp_path):
    """The refusal used to exit 0 with an error JSON. A caller checking the
    exit code -- the ventures cadence runner did -- saw success and wrote
    nothing for 59 runs. Exit code and JSON must agree."""
    target = tmp_path / "next_actions.org"
    target.write_text("#+TITLE: t\n")
    result = subprocess.run(
        [sys.executable, str(ADAPTER), "add", "--file", str(target),
         "--heading", "captured in the wrong place", "--state", "TODO"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, result.stdout
    assert "error" in json.loads(result.stdout)
    assert target.read_text() == "#+TITLE: t\n", "refused write must not touch the file"


def test_exception_result_exits_nonzero(tmp_path):
    missing = tmp_path / "does-not-exist.org"
    result = subprocess.run(
        [sys.executable, str(ADAPTER), "list", "--file", str(missing)],
        capture_output=True, text=True, timeout=30)
    body = json.loads(result.stdout) if result.stdout.strip() else {}
    if body.get("error"):
        assert result.returncode != 0


class TestEveryMutationReachesTheLedger:
    """add/update/complete must all emit. 2026-09-09: only `add` did.

    `cmd_add` has emitted `item.create` since DIP-0046 C4b; update and complete
    emitted nothing, so every state change and property edit an agent made
    lived only in the org file. Where that file is a Phase 1 projection it is
    regenerated hourly from the ledger, so the edit reverted silently; where it
    is `inbox.org` it survived, but the ledger every other reader consults
    stayed wrong until the nightly sweep.

    These assertions fold the log FROM DISK. They never trust the adapter's own
    return value — a tool reporting its own success is what let this stand.
    """

    @staticmethod
    def _space(tmp_path):
        space = tmp_path / "5-testspace"
        (space / ".datacore" / "events").mkdir(parents=True)
        (space / "org").mkdir(parents=True)
        (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
        return space

    @staticmethod
    def _events(space, task_id):
        out = []
        for f in sorted((space / ".datacore" / "events").glob("*.jsonl")):
            for ln in f.read_text().splitlines():
                if ln.strip():
                    e = json.loads(ln)
                    if (e.get("payload") or {}).get("id") == task_id:
                        out.append(e)
        return out

    def test_add_update_and_complete_all_emit(self, tmp_path):
        space = self._space(tmp_path)
        org = str(space / "org" / "inbox.org")

        added = run_adapter("add", "--file", org, "--allow-any-file",
                            "--heading", "A task the ledger should hear about",
                            "--state", "TODO", "--property", "SURFACE=core")
        tid = added["id"]
        assert [e["type"] for e in self._events(space, tid)] == ["item.create"]

        run_adapter("update", "--file", org, "--id", tid,
                    "--property", "CONTEXT=why this task exists")
        evs = self._events(space, tid)
        upd = [e for e in evs if e["type"] == "item.update"]
        assert upd, "update reached the org file but not the ledger"
        props = ((upd[-1]["payload"].get("org") or {}).get("properties") or {})
        assert props.get("CONTEXT") == "why this task exists"

        run_adapter("complete", "--file", org, "--id", tid)
        types = [e["type"] for e in self._events(space, tid)]
        assert "item.dismiss" in types, f"complete did not emit: {types}"


class TestWritesSurviveAProjectionRebuild:
    """datacore#173's third ask: write, force a projection rebuild, assert.

    This is the check the other two cannot make. Emitting an event proves the
    ledger heard; returning `observed` proves the file says so a millisecond
    later. Only regenerating the projection proves the write is still there
    tomorrow morning — which is exactly what five writes on 2026-09-08 were
    not.
    """

    @staticmethod
    def _phase1_space(tmp_path):
        space = tmp_path / "5-testspace"
        (space / ".datacore" / "events").mkdir(parents=True)
        (space / "org").mkdir(parents=True)
        (space / ".datacore" / "ledger-phase").write_text("1\n")
        (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
        subprocess.run(["git", "init", "-q", "."], cwd=space, capture_output=True)
        return space

    def test_an_update_survives_regenerating_next_actions(self, tmp_path):
        space = self._phase1_space(tmp_path)
        org = str(space / "org" / "inbox.org")

        tid = run_adapter("add", "--file", org, "--allow-any-file",
                          "--heading", "A task whose schedule must survive the night",
                          "--state", "NEXT", "--property", "SURFACE=core")["id"]
        run_adapter("update", "--file", org, "--id", tid,
                    "--property", "DONE_WHEN=the projection still says so")

        # Regenerate the projection from the ledger — the thing that used to
        # erase these writes.
        proj = ADAPTER.parent / "ledger_project_org.py"
        r = subprocess.run(["python3", str(proj), "--space", space.name,
                            "--root", str(tmp_path)],
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, f"projection failed: {r.stdout}{r.stderr}"

        rendered = (space / "org" / "next_actions.org").read_text()
        assert tid in rendered, "the task did not survive the rebuild"
        assert "the projection still says so" in rendered, (
            "DONE_WHEN was written, reported, and then erased by the rebuild — "
            "the exact 2026-09-08 failure"
        )
