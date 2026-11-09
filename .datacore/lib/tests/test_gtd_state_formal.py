"""Findings from the Lean model DatacoreSpec/GtdState.lean, replayed against the real code.

Each test pins one counterexample the model found in the pre-fix code. The
ledger is folded FROM DISK; nothing trusts the adapter's own return value.

1. `complete` on a repeater task: org-workspace advances SCHEDULED and reopens
   the task as TODO, but the adapter emitted `item.dismiss` anyway, so a live
   recurring task was terminally dismissed in the ledger.
2. `update --state DONE|CANCELLED` on an authored file emitted only
   `item.update`, so the ledger item stayed live with state DONE (DIP-0009
   v2.0 ruling 5: DONE and CANCELLED both dismiss; DEFERRED never does).
3. `dedup_tasks.retire_duplicate` on a repeater: the CANCELLED transition
   advanced the date instead, and the still-open task was stamped DEDUPE_OF.
4. `dedup_tasks`: the documented "lowest id" tie-break was dead code.
5. `task_cleanup dup-ids`: minted `<id>-copy<N>`, which a rerun mints again;
   the second copy made the file unloadable. DIP-0009 ids are UUIDs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ADAPTER = LIB / "org_workspace_adapter.py"
sys.path.insert(0, str(LIB))

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = (tmp_path / "runtime-state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ACTOR", "formal-test")
    return tmp_path


def _space(root: Path, phase: int | None = None) -> Path:
    space = root / "5-formal"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org").mkdir()
    if phase is not None:
        (space / ".datacore" / "ledger-phase").write_text(f"{phase}\n")
    (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
    return space


def _run(*args):
    r = subprocess.run([sys.executable, str(ADAPTER), *args], capture_output=True, text=True)
    body = json.loads(r.stdout)
    assert not (isinstance(body, dict) and body.get("error")), body
    return body


def _item(space: Path, tid: str):
    from ledger.fold import fold
    from ledger.log import read_events
    return fold(read_events(space)).items.get(tid)


def _add(org: str, heading: str, scheduled: str | None = None) -> str:
    extra = ["--scheduled", scheduled] if scheduled else []
    return _run("add", "--file", org, "--allow-any-file", "--heading", heading, *extra)["id"]


def _make_repeater(org: str, tid_date: str = "2026-09-21") -> None:
    p = Path(org)
    text = re.sub(rf"(SCHEDULED: <{tid_date} \w+)>", r"\1 +1w>", p.read_text())
    assert "+1w>" in text
    p.write_text(text)


# ---------------------------------------------------------------- adapter

@pytest.mark.parametrize("phase", [None, 1])
def test_complete_on_a_repeater_keeps_the_ledger_item_live(env, phase):
    space = _space(env, phase)
    org = str(space / "org" / "inbox.org")
    tid = _add(org, "Weekly review", scheduled="2026-09-21")
    _make_repeater(org)

    out = _run("complete", "--file", org, "--id", tid)

    assert out["observed"]["STATE"] == "TODO"          # org reopened the cycle
    item = _item(space, tid)
    assert item.status != "dismissed", "a live recurring task was dismissed in the ledger"
    assert "2026-09-28" in str(item.payload.get("scheduled")), item.payload.get("scheduled")


@pytest.mark.parametrize("phase", [None, 1])
@pytest.mark.parametrize("state,kind", [("DONE", "done"), ("CANCELLED", "dropped")])
def test_update_to_done_or_cancelled_dismisses(env, phase, state, kind):
    space = _space(env, phase)
    org = str(space / "org" / "inbox.org")
    tid = _add(org, f"close me via update {state}")

    _run("update", "--file", org, "--id", tid, "--state", state)

    item = _item(space, tid)
    assert item.status == "dismissed", f"org says {state}, ledger says {item.status}"
    assert item.closed_kind == kind


def test_update_to_deferred_never_dismisses(env):
    space = _space(env, 1)
    org = str(space / "org" / "inbox.org")
    tid = _add(org, "bench me")
    _run("update", "--file", org, "--id", tid, "--state", "DEFERRED")
    item = _item(space, tid)
    assert item.status != "dismissed"
    assert item.payload.get("state") == "DEFERRED"


def test_update_to_done_on_a_repeater_does_not_dismiss(env):
    space = _space(env, 1)
    org = str(space / "org" / "inbox.org")
    tid = _add(org, "Monthly invoice run", scheduled="2026-09-21")
    _make_repeater(org)
    _run("update", "--file", org, "--id", tid, "--state", "DONE")
    item = _item(space, tid)
    assert item.status != "dismissed"
    assert "2026-09-28" in str(item.payload.get("scheduled"))


def test_complete_without_an_id_emits_no_event(env):
    space = _space(env, 1)
    habits = space / "org" / "habits.org"
    habits.write_text("* TODO Take B12\n  SCHEDULED: <2026-09-21 Mon .+1d>\n")
    _run("complete", "--file", str(habits), "--title", "Take B12")
    lines = [ln for f in (space / ".datacore" / "events").glob("*.jsonl")
             for ln in f.read_text().splitlines() if ln.strip()]
    assert not [ln for ln in lines if json.loads(ln).get("payload", {}).get("id") is None]


# ---------------------------------------------------------------- dedup_tasks

def _block(ident: str, sched: str = "", props: str = "") -> str:
    s = f"SCHEDULED: {sched}\n" if sched else ""
    return f"* TODO Weekly review\n{s}:PROPERTIES:\n:ID: {ident}\n{props}:END:\n"


def test_retire_duplicate_refuses_a_repeater(env):
    import org_transaction as tx
    from dedup_tasks import retire_duplicate
    p = env / "next_actions.org"
    p.write_text(_block("keep-id", "<2026-09-21 Mon +1w>") + _block("dup-id", "<2026-09-21 Mon +1w>"))

    @tx.serialized
    def go():
        ws = tx.SafeOrgWorkspace()
        ws.load(p)
        ok = retire_duplicate(ws, ws.find_by_id("keep-id"), ws.find_by_id("dup-id"))
        ws.save()
        return ok
    assert go() is False
    text = p.read_text()
    assert "DEDUPE_OF" not in text and "2026-09-28" not in text


def test_retire_duplicate_still_cancels_a_plain_duplicate(env):
    import org_transaction as tx
    from dedup_tasks import retire_duplicate
    p = env / "next_actions.org"
    p.write_text(_block("keep-id") + _block("dup-id"))

    @tx.serialized
    def go():
        ws = tx.SafeOrgWorkspace()
        ws.load(p)
        assert retire_duplicate(ws, ws.find_by_id("keep-id"), ws.find_by_id("dup-id"))
        ws.save()
        return ws.find_by_id("dup-id").todo
    assert go() == "CANCELLED"


def test_rank_group_breaks_ties_by_lowest_id(env):
    import org_transaction as tx
    from dedup_tasks import rank_group
    p = env / "next_actions.org"
    p.write_text(_block("c-id") + _block("a-id") + _block("b-id"))

    @tx.serialized
    def go():
        ws = tx.SafeOrgWorkspace()
        ws.load(p)
        return [n.id() for n in rank_group(list(ws.all_nodes()))]
    assert go() == ["a-id", "b-id", "c-id"]


def test_rank_group_prefers_more_properties_then_latest_schedule(env):
    import org_transaction as tx
    from dedup_tasks import rank_group
    p = env / "next_actions.org"
    p.write_text(_block("a-id", "<2026-09-01 Tue>") + _block("b-id", "<2026-09-10 Thu>")
                 + _block("c-id", "<2026-09-01 Tue>", ":CONTEXT: why\n"))

    @tx.serialized
    def go():
        ws = tx.SafeOrgWorkspace()
        ws.load(p)
        return [n.id() for n in rank_group(list(ws.all_nodes()))]
    assert go() == ["c-id", "b-id", "a-id"]


# ---------------------------------------------------------------- task_cleanup

def test_dup_id_reassignment_mints_fresh_uuids_across_reruns(env):
    root = env / "data"
    org = root / "1-x" / "org"
    org.mkdir(parents=True)

    def t(heading, ident):
        return f"* TODO {heading}\n:PROPERTIES:\n:ID: {ident}\n:END:\n"
    shared = str(uuid.uuid4())
    (org / "next_actions.org").write_text(t("Canonical task heading long enough", shared))
    (org / "inbox.org").write_text(t("Different wording of the routed copy", shared))

    def cleanup():
        r = subprocess.run([sys.executable, str(LIB / "task_cleanup.py"), "dup-ids", "--apply"],
                           cwd=root, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "errors: 0" in r.stdout, r.stdout

    cleanup()
    # a second routing copy of the same task lands in the inbox later
    (org / "inbox.org").write_text((org / "inbox.org").read_text()
                                   + t("Another routed copy of the task", shared))
    cleanup()

    ids = re.findall(r"^:ID: (\S+)$", (org / "inbox.org").read_text(), re.M)
    assert len(ids) == 2 and len(set(ids)) == 2, ids
    assert all(UUID_RE.match(i) for i in ids), ids
    assert shared not in ids

    import org_transaction as tx

    @tx.serialized
    def load_all():
        ws = tx.SafeOrgWorkspace()
        for f in sorted(org.glob("*.org")):
            ws.load(f)
    load_all()  # no duplicate-id refusal anywhere


def test_cleanup_close_skips_a_repeater(env):
    import task_cleanup
    root = env / "data"
    org = root / "1-x" / "org"
    org.mkdir(parents=True)
    f = org / "next_actions.org"
    f.write_text("* TODO A weekly repeating chore heading\nSCHEDULED: <2026-06-01 Mon +1w>\n"
                 ":PROPERTIES:\n:ID: rep-id\n:END:\n")
    import os
    cwd = os.getcwd()
    os.chdir(root)
    try:
        done, errors = task_cleanup.apply_actions([{
            'kind': 'close', 'file': '1-x/org/next_actions.org', 'id': 'rep-id',
            'state': 'CANCELLED', 'reason': 'test', 'heading': 'x'}])
    finally:
        os.chdir(cwd)
    assert done == 0 and errors
    text = f.read_text()
    assert "CLOSED_REASON" not in text and "2026-06-01" in text
