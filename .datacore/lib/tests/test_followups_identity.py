"""Owner follow-up decisions Q2 and Q3 (second board, 2026-09-23).

Q2  every ledger writer / identity caller resolves the actor with
    `actor_identity.this_actor(strict=True)` AT STARTUP and fails fast, naming
    identity.env and the registry. An explicit `--actor`, `DATACORE_ACTOR`, or
    a registry writer name still wins. Before Q2 they resolved non-strictly
    (hostname fallback) and failed only later, at `EventLog.append` (L10).
Q3  with `.datacore/ledger-edit-protocol` = 2, `projection_state` and
    `ledger_phase1_prepare` detect CHANGED fields type-strictly (canonical JSON
    bytes, as `edits.merge_values(strict=True)`), so an Org edit from 1 to True
    is proposed instead of silently read as "unchanged". Under protocol 1 the
    old Python `==` is kept.

Lean: DatacoreSpec/GitFleet.lean §5 (`startup_resolves_or_refuses`, ...),
DatacoreSpec/LedgerPolicy.lean (`Merge.changed_strict_sees_edit`, ...).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import actor_identity

LIB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, LIB / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def undeclared(monkeypatch):
    """A host with no DATACORE_ACTOR, no identity.env and no registry row."""
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    monkeypatch.setattr(actor_identity, "resolve", lambda *a, **kw: (None, "none"))


def _refused(capsys, rc):
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "identity.env" in err and "infrastructure.yaml" in err


# ---------------------------------------------------------------- Q2: CLIs ---

def test_ledger_claim_refuses_at_startup_on_an_undeclared_host(tmp_path, undeclared, monkeypatch, capsys):
    m = _load("ledger_claim")
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["ledger_claim.py", "--space", str(tmp_path)])
    monkeypatch.setattr(m, "read_events", lambda *_: pytest.fail("read the ledger before resolving"))
    _refused(capsys, m.main())


def test_ledger_claim_explicit_actor_still_wins(tmp_path, undeclared, monkeypatch):
    m = _load("ledger_claim")
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["ledger_claim.py", "--space", str(tmp_path), "--actor", "someone"])
    assert m.main() == 0


def test_phase1_prepare_refuses_at_startup(tmp_path, undeclared, capsys, monkeypatch):
    m = _load("ledger_phase1_prepare")
    monkeypatch.setattr(m, "plan", lambda *_: pytest.fail("planned before resolving"))
    _refused(capsys, m.main(["--root", str(tmp_path), "--space", "9-x"]))


def test_phase1_prepare_explicit_actor_is_not_resolved(tmp_path, undeclared, monkeypatch):
    m = _load("ledger_phase1_prepare")
    monkeypatch.setattr(m, "plan", lambda *_: {"events": [], "unmatched": []})
    assert m.main(["--root", str(tmp_path), "--space", "9-x", "--actor", "someone"]) == 0


@pytest.mark.parametrize("name,extra", [
    ("ledger_accept_authored", ["--item", "x", "--field", "title"]),
    ("ledger_resolve_conflict", ["--item", "x"]),
])
def test_repair_tools_refuse_at_startup(tmp_path, undeclared, capsys, name, extra):
    m = _load(name)
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    _refused(capsys, m.main(["--space", str(tmp_path), *extra]))


@pytest.mark.parametrize("name,extra", [
    ("ledger_accept_authored", ["--item", "x", "--field", "title"]),
    ("ledger_resolve_conflict", ["--item", "x"]),
])
def test_repair_tools_explicit_actor_still_wins(tmp_path, undeclared, name, extra):
    m = _load(name)
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    with pytest.raises(SystemExit, match="not in the ledger"):   # got past identity
        m.main(["--space", str(tmp_path), *extra, "--actor", "someone"])


def test_ledger_cli_append_refuses_cleanly_at_startup(tmp_path, undeclared, monkeypatch, capsys):
    m = _load("ledger_cli")
    monkeypatch.setattr(m, "EventLog", lambda *a, **kw: pytest.fail("opened a log before resolving"))
    monkeypatch.setattr(sys, "argv", ["ledger_cli.py", "append", "--space", str(tmp_path),
                                      "--type", "item.create", "--payload", '{"id": "t"}'])
    with pytest.raises(SystemExit) as exc:
        m.main()
    _refused(capsys, exc.value.code)


def test_ledger_cli_read_commands_need_no_identity(tmp_path, undeclared, monkeypatch):
    m = _load("ledger_cli")
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["ledger_cli.py", "balances", "--space", str(tmp_path)])
    m.main()


def test_ledger_seal_emit_refuses_at_startup_status_does_not(tmp_path, undeclared, monkeypatch, capsys):
    m = _load("ledger_seal")
    monkeypatch.setattr(m, "cmd_emit", lambda *a: pytest.fail("emitted before resolving"))
    monkeypatch.setattr(sys, "argv", ["ledger_seal.py", "emit", "--space", str(tmp_path)])
    _refused(capsys, m.main())
    monkeypatch.setattr(m, "cmd_status", lambda *a: 0)
    monkeypatch.setattr(sys, "argv", ["ledger_seal.py", "status", "--space", str(tmp_path)])
    assert m.main() == 0


def test_ledger_ingest_org_refuses_before_sweeping(tmp_path, undeclared, monkeypatch, capsys):
    m = _load("ledger_ingest_org")
    (tmp_path / "9-x" / "org").mkdir(parents=True)
    monkeypatch.setattr(m, "scan", lambda *_: pytest.fail("swept before resolving"))
    monkeypatch.setattr(m, "ensure_ids", lambda *_: pytest.fail("swept before resolving"))
    monkeypatch.setattr(sys, "argv", ["ledger_ingest_org.py", "--root", str(tmp_path)])
    _refused(capsys, m.main())


# ------------------------------------------------------- Q2: library callers ---

def test_executor_default_actor_is_strict(undeclared):
    from executors import base
    with pytest.raises(actor_identity.UndeclaredActor):
        base._default_actor()


def test_executor_run_on_an_undeclared_host_invokes_nothing(undeclared):
    from executors import base

    class Probe(base.Executor):
        name = "probe"

        def _invoke(self, prompt, timeout_s):
            pytest.fail("invoked before resolving the actor")
    result = Probe().run("hi")
    assert result.error and "identity.env" in result.error


def test_env_actor_still_wins_everywhere(monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "declared-writer")
    from executors import base
    assert base._default_actor() == "declared-writer"
    assert actor_identity.this_actor(strict=True) == "declared-writer"


def test_adapter_resolves_strictly_before_a_phase1_write(tmp_path, undeclared, monkeypatch):
    """Before Q2 the adapter resolved to the hostname and reached sync_generated,
    failing only at append; now it refuses before planning any event."""
    import org_workspace_adapter as adapter
    import ledger.projection_state as ps
    space = tmp_path / "9-x"
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    target = space / "org" / "next_actions.org"
    target.write_text("")
    import org_space
    monkeypatch.setattr(org_space, "ledger_space_for_file", lambda *_: space)
    monkeypatch.setattr(ps, "sync_generated", lambda *a, **kw: pytest.fail("planned before resolving"))
    with pytest.raises(actor_identity.UndeclaredActor):
        adapter._ledger_emit(target, "item.update", {"id": "x"})


def test_adapter_phase0_mirror_stays_optional(tmp_path, undeclared, monkeypatch):
    import org_workspace_adapter as adapter
    space = tmp_path / "9-x"
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "events").mkdir(parents=True)
    target = space / "org" / "inbox.org"
    target.write_text("")
    import org_space
    monkeypatch.setattr(org_space, "ledger_space_for_file", lambda *_: space)
    assert adapter._ledger_emit(target, "item.create", {"id": "x"}) is None
    assert not any((space / ".datacore" / "events").iterdir())


# ------------------------------------------------------------------ Q3 ---

def _typed_space(tmp_path, protocol):
    from ledger.fold import fold
    from ledger.log import EventLog, read_events
    from ledger.projector import project
    from ledger.projection_state import STATE, base_document
    space = tmp_path / "9-drill"
    (space / "org").mkdir(parents=True)
    (space / ".datacore").mkdir(exist_ok=True)
    (space / ".datacore/ledger-edit-protocol").write_text(f"{protocol}\n")
    EventLog(space, "writer").append("item.create", {
        "id": "one", "title": "t", "state": "TODO", "space": "9-drill", "level": 1, "tags": [],
        "org": {"body": "b", "properties": {"A": "x"}, "priority": None}})
    text = project(fold(read_events(space)), space=space.name).text
    return space, text, STATE, base_document


def _inject_true(monkeypatch, module, authored):
    """The authored file carries `level: True` where the ledger has `1`.

    Org text parses every field to one type, so the 1/True pair cannot be typed
    into a file; it is injected at the snapshot boundary, which is exactly where
    the changed-field comparison reads it."""
    real = module.snapshot

    def snap(text, space):
        out = real(text, space)
        if text == authored:
            out["items"]["one"]["level"] = True
        return out
    monkeypatch.setattr(module, "snapshot", snap)


@pytest.mark.parametrize("protocol,proposed", [(1, False), (2, True)])
def test_projection_state_detects_a_1_to_true_edit_only_under_protocol_2(tmp_path, monkeypatch, protocol, proposed):
    import ledger.projection_state as ps
    from ledger.fold import fold
    from ledger.log import read_events
    space, text, STATE, base_document = _typed_space(tmp_path, protocol)
    authored = text + "\n\n"          # differs as bytes, parses identically
    (space / "org/next_actions.org").write_text(authored)
    (space / STATE).parent.mkdir(parents=True)
    (space / STATE).write_text(base_document(text))
    _inject_true(monkeypatch, ps, authored)
    result = ps.sync_generated(space, fold(read_events(space)), "writer")
    assert result["updated"] == (1 if proposed else 0)
    updates = [e for e in read_events(space) if e.type == "item.update"]
    if proposed:
        assert updates[-1].payload["level"] is True
        assert updates[-1].payload["_merge"]["version"] == 2
        assert fold(read_events(space)).items["one"].payload["level"] is True
    else:
        assert updates == []


@pytest.mark.parametrize("protocol,proposed", [(1, False), (2, True)])
def test_phase1_prepare_detects_a_1_to_true_edit_only_under_protocol_2(tmp_path, monkeypatch, protocol, proposed):
    m = _load("ledger_phase1_prepare")
    space, text, _, _ = _typed_space(tmp_path, protocol)
    authored = text + "\n\n"          # the rendered text would otherwise be identical
    (space / "org/next_actions.org").write_text(authored)
    _inject_true(monkeypatch, m, authored)
    plan = m.plan(space)
    if not proposed:
        assert plan["events"] == []
        return
    [event] = plan["events"]
    assert event["payload"]["level"] is True
    assert event["payload"]["_merge"]["version"] == 2
    assert m.apply(space, "writer", plan) == 1


def test_changed_fields_is_loose_under_1_and_strict_under_2():
    from ledger.projection_state import changed_fields
    fields, existing = {"a": True, "b": 1.0, "c": "s"}, {"a": 1, "b": 1, "c": "s"}
    assert changed_fields(fields, existing, strict=False) == {}
    assert changed_fields(fields, existing, strict=True) == {"a": True, "b": 1.0}


def test_edit_strict_reads_the_space_protocol(tmp_path):
    from ledger.projection_state import edit_strict
    space = tmp_path / "9-x"
    (space / ".datacore").mkdir(parents=True)
    assert edit_strict(space) is False                       # absent
    for value, want in (("1", False), ("2", True), ("junk", False)):
        (space / ".datacore/ledger-edit-protocol").write_text(value + "\n")
        assert edit_strict(space) is want
