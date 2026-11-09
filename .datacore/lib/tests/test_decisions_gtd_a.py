"""Owner decisions G1, G2, G4 and G7 (2026-09-23), applied to the gtd-a area.

G1  Warn only: the adapter warns (stderr and a `warnings` list in its JSON)
    when a requested transition is not in the DIP-0009 v2.0 table, then
    performs it exactly as before.
G2  DEFERRED→CANCELLED is part of the adapter's spec relation, so it does not
    warn. (The DIP table itself lives in the dips repo and is edited there.)
G4  task_cleanup dup-ids never reassigns an id that appears in a GENERATED
    next_actions.org (Phase 1). That covers the inbox.org / generated
    next_actions.org pair, which shares ids by design.
G7  ledger_project_org watches the flip-time header copy before its first
    write, so a writer that creates the file between the existence check and
    the write is refused instead of silently overwritten.

Lean: DatacoreSpec/GtdState.lean, section 5 (`warn_iff_not_spec`, ...).
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ADAPTER = LIB / "org_workspace_adapter.py"
sys.path.insert(0, str(LIB))


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = (tmp_path / "runtime-state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ACTOR", "decisions-test")
    return tmp_path


def _org(root: Path, state: str) -> tuple[str, str]:
    space = root / "5-decisions"
    (space / "org").mkdir(parents=True)
    tid = str(uuid.uuid4())
    f = space / "org" / "someday.org"
    f.write_text(f"* {state} A task heading\n:PROPERTIES:\n:ID: {tid}\n:END:\n")
    return str(f), tid


def _run(*args):
    r = subprocess.run([sys.executable, str(ADAPTER), *args], capture_output=True, text=True)
    body = json.loads(r.stdout)
    assert r.returncode == 0 and not body.get("error"), (body, r.stderr)
    return body, r.stderr


# ------------------------------------------------------------------ G1 / G2

@pytest.mark.parametrize("start,target", [
    ("REVIEW", "TODO"), ("REVIEW", "WAITING"),
    ("DEFERRED", "NEXT"), ("DEFERRED", "WAITING"), ("DEFERRED", "REVIEW"),
    ("DEFERRED", "DONE"),
])
def test_update_outside_the_table_warns_and_still_transitions(env, start, target):
    org, tid = _org(env, start)
    body, err = _run("update", "--file", org, "--id", tid, "--state", target)
    assert body["observed"]["STATE"] == target            # performed as before
    assert any(f"{start}→{target}" in w for w in body.get("warnings", [])), body
    assert "DIP-0009" in err and f"{start}→{target}" in err, err


def test_complete_from_deferred_warns_and_still_completes(env):
    org, tid = _org(env, "DEFERRED")
    body, err = _run("complete", "--file", org, "--id", tid)
    assert body["observed"]["STATE"] == "DONE"
    assert any("DEFERRED→DONE" in w for w in body.get("warnings", [])), body
    assert "DEFERRED→DONE" in err


@pytest.mark.parametrize("start,target", [
    ("TODO", "NEXT"), ("NEXT", "WAITING"), ("WAITING", "REVIEW"),
    ("REVIEW", "NEXT"), ("REVIEW", "DONE"), ("DEFERRED", "TODO"),
    ("DEFERRED", "CANCELLED"),                               # G2
])
def test_update_inside_the_table_is_silent(env, start, target):
    org, tid = _org(env, start)
    body, err = _run("update", "--file", org, "--id", tid, "--state", target)
    assert body["observed"]["STATE"] == target
    assert not body.get("warnings"), body
    assert "DIP-0009" not in err


def test_the_adapter_spec_relation_is_the_v2_table_plus_g2():
    import org_workspace_adapter as a
    assert a.dip0009_allows("DEFERRED", "CANCELLED")        # G2
    assert not a.dip0009_allows("DEFERRED", "DONE")
    assert not a.dip0009_allows("REVIEW", "TODO")
    assert not a.dip0009_allows("TODO", "QUEUED")           # retired keyword
    assert not a.dip0009_allows("DONE", "TODO")             # terminal
    # 23 v2.0 moves + DEFERRED→CANCELLED
    assert sum(len(v) for v in a.DIP0009_V2_TRANSITIONS.values()) == 24


# ----------------------------------------------------------------------- G4

def _task(heading, ident):
    return f"* TODO {heading}\n:PROPERTIES:\n:ID: {ident}\n:END:\n"


def test_dup_ids_never_reassigns_an_id_in_a_generated_next_actions(env, monkeypatch):
    import task_cleanup
    root = env / "data"
    shared, cross = str(uuid.uuid4()), str(uuid.uuid4())
    sp = root / "1-gen"
    (sp / "org").mkdir(parents=True)
    (sp / ".datacore").mkdir()
    (sp / ".datacore" / "ledger-phase").write_text("1\n")
    (sp / "org" / "next_actions.org").write_text(
        _task("Projected capture heading long enough", shared)
        + _task("Projected task also held elsewhere", cross))
    (sp / "org" / "inbox.org").write_text(_task("Projected capture heading long enough", shared))
    other = root / "2-authored" / "org"
    other.mkdir(parents=True)
    (other / "inbox.org").write_text(_task("Projected task also held elsewhere", cross))
    monkeypatch.chdir(root)

    actions = task_cleanup.plan_dup_ids(task_cleanup.scan())
    assert actions == [], actions


def test_dup_ids_still_reassigns_in_a_phase0_space(env, monkeypatch):
    import task_cleanup
    root = env / "data"
    shared = str(uuid.uuid4())
    org = root / "1-authored" / "org"
    org.mkdir(parents=True)
    (org / "next_actions.org").write_text(_task("Canonical task heading long enough", shared))
    (org / "inbox.org").write_text(_task("Different wording of the routed copy", shared))
    monkeypatch.chdir(root)

    actions = task_cleanup.plan_dup_ids(task_cleanup.scan())
    assert [a["file"] for a in actions] == ["1-authored/org/inbox.org"], actions


# ----------------------------------------------------------------------- G7

def test_header_copy_first_write_refuses_a_racing_writer(env, monkeypatch):
    import ledger_project_org as lpo
    import org_transaction as tx

    space = env / "5-hdr"
    (space / "org").mkdir(parents=True)
    (space / ".datacore").mkdir()
    target = space / "org" / "next_actions.org"
    target.write_text("#+TITLE: Next\n* TODO x\n")
    copy = space / lpo.HEADER_COPY

    real_write = lpo.write_org_text

    def racing_write(path, text):
        # A competitor creates the copy after the existence check and
        # before our write reaches the transaction.
        if Path(path) == copy and not copy.exists():
            copy.write_text("#+TITLE: Someone else's header\n")
        return real_write(path, text)

    monkeypatch.setattr(lpo, "write_org_text", racing_write)

    @tx.serialized
    def run():
        return lpo._with_org_header(space, target, "* TODO y\n", remember=True)

    with pytest.raises(tx.RecoveryRequired):
        run()
    assert copy.read_text() == "#+TITLE: Someone else's header\n"
