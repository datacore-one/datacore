"""Owner decisions L7-L10 (2026-09-23), ledger-b area.

L7  type-strict three-way merge for NEW conditional edits (`_merge.version: 2`),
    enabled per space by `.datacore/ledger-edit-protocol` = 2.
L8  EventLog.append refuses NaN / +-Infinity before stamping.
L9  ledger_transport._push_with_retry and knowledge_commit._push_commit run
    git_relay.publication_forks before pushing.
L10 an undeclared host may not append under its guessed (hostname) actor.

Lean: DatacoreSpec/LedgerPolicy.lean (Merge.merge_symm_strict, ...),
DatacoreSpec/GitFleet.lean (never_push_a_fork_all_pushers, strict_actor_*).
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest

import actor_identity
from ledger import fork
from ledger.edits import EditConflict, apply_condition, conditional_payload, merge_values, require_edit_protocol
from ledger.fold import fold
from ledger.log import EventLog, read_events


def _space(tmp_path: Path, protocol: str | None = None) -> Path:
    space = tmp_path / "9-drill"
    (space / ".datacore").mkdir(parents=True)
    if protocol is not None:
        (space / ".datacore/ledger-edit-protocol").write_text(protocol + "\n")
    return space


# --- L7: type-strict merge, versioned -------------------------------------

def test_strict_merge_keeps_an_edit_from_one_to_true():
    """Lean: Merge.strict_edit_kept (the fix for bool_one_edit_lost)."""
    assert merge_values(1, True, 1, strict=True) is True
    kept = merge_values(1, 1.0, 1, strict=True)
    assert isinstance(kept, float)


def test_loose_merge_is_unchanged_for_version_one():
    """Every existing event folds exactly as before: the default is Python ==."""
    kept = merge_values(1, True, 1)
    assert kept == 1 and kept is not True


@pytest.mark.parametrize("b,l,r", [
    (0, 1, True), (0, True, 1), (1, 1.0, True), ({"a": 0}, {"a": 1}, {"a": True}),
    ({"a": 1}, {"a": True}, {"a": 1}), ([1], [True], [1]), (1, True, 1),
])
def test_strict_merge_is_symmetric_on_the_nose(b, l, r):
    """Lean: Merge.merge_symm_strict -- equal results, not merely == results."""
    def run(x, y):
        try:
            import json
            return ("ok", json.dumps(merge_values(b, x, y, strict=True), sort_keys=True))
        except EditConflict:
            return ("conflict", None)
    assert run(l, r) == run(r, l)


class _Item:
    def __init__(self, payload):
        self.id = "one"
        self.payload = payload
        self.status = "created"
        self.owner = None


def test_version_two_condition_applies_strictly_version_one_loosely():
    item = _Item({"id": "one", "n": 1})
    v1 = conditional_payload(item, {"n": True})
    assert v1["_merge"]["version"] == 1
    assert apply_condition(item, v1)["n"] is not True     # historical meaning kept
    v2 = conditional_payload(item, {"n": True}, version=2)
    assert apply_condition(item, v2)["n"] is True


def test_terminal_version_two_sees_a_type_change_as_content_change():
    item = _Item({"id": "one", "n": 1})
    stale = conditional_payload(_Item({"id": "one", "n": True}), {"kind": "done"}, terminal=True, version=2)
    with pytest.raises(EditConflict):
        apply_condition(item, stale)


def test_protocol_value_selects_the_emitted_version(tmp_path):
    assert require_edit_protocol(_space(tmp_path / "a", "1")) == 1
    assert require_edit_protocol(_space(tmp_path / "b", "2")) == 2
    with pytest.raises(EditConflict):
        require_edit_protocol(_space(tmp_path / "c"))
    with pytest.raises(EditConflict):
        require_edit_protocol(_space(tmp_path / "d", "3"))


def _edit_true(space: Path):
    log = EventLog(space, "writer", sign=False)
    log.append("item.create", {"id": "one", "title": "t", "n": 1})
    item = fold(read_events(space)).items["one"]
    return log.append("item.update", conditional_payload(item, {"n": True}))


def test_protocol_two_stamps_version_two_and_the_edit_survives_the_fold(tmp_path):
    space = _space(tmp_path, "2")
    event = _edit_true(space)
    assert event.payload["_merge"]["version"] == 2
    assert fold(read_events(space)).items["one"].payload["n"] is True


def test_protocol_one_keeps_version_one(tmp_path):
    space = _space(tmp_path, "1")
    event = _edit_true(space)
    assert event.payload["_merge"]["version"] == 1
    assert fold(read_events(space)).items["one"].payload["n"] is not True


def test_version_two_edit_refused_where_readers_are_not_upgraded(tmp_path):
    space = _space(tmp_path, "1")
    log = EventLog(space, "writer", sign=False)
    log.append("item.create", {"id": "one", "title": "t", "n": 1})
    item = fold(read_events(space)).items["one"]
    with pytest.raises(EditConflict, match="ledger-edit-protocol=2"):
        log.append("item.update", conditional_payload(item, {"n": True}, version=2))


def test_append_does_not_mutate_the_callers_payload(tmp_path):
    space = _space(tmp_path, "2")
    log = EventLog(space, "writer", sign=False)
    log.append("item.create", {"id": "one", "title": "t", "n": 1})
    payload = conditional_payload(fold(read_events(space)).items["one"], {"n": True})
    log.append("item.update", payload)
    assert payload["_merge"]["version"] == 1


# --- L8: refuse NaN / Infinity at append ----------------------------------

@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_append_refuses_non_finite_numbers(tmp_path, bad):
    space = _space(tmp_path)
    log = EventLog(space, "writer", sign=False)
    log.append("item.create", {"id": "ok", "title": "t"})
    before = log.path.read_bytes()
    with pytest.raises(ValueError, match="NaN or Infinity"):
        log.append("item.update", {"id": "ok", "nested": {"x": [1, bad]}})
    assert log.path.read_bytes() == before
    log.append("item.update", {"id": "ok", "x": 1.5})  # the chain continues
    assert [e.seq for e in read_events(space)] == [0, 1]


# --- L10: an undeclared host cannot append under its hostname -------------

@pytest.fixture
def undeclared(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    monkeypatch.setattr(actor_identity, "IDENTITY_FILE", tmp_path / "no-identity.env")
    monkeypatch.setattr(actor_identity, "INFRA", tmp_path / "no-infra.yaml")
    monkeypatch.setattr(actor_identity, "short_hostname", lambda: "sharedhost")
    return tmp_path


def test_undeclared_host_refuses_to_append_as_its_hostname(undeclared):
    space = _space(undeclared)
    log = EventLog(space, "sharedhost", sign=False)
    with pytest.raises(actor_identity.UndeclaredActor, match="DATACORE_ACTOR"):
        log.append("item.create", {"id": "x", "title": "t"})
    assert not log.path.exists()


def test_declared_host_may_append(undeclared, monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "sharedhost")
    space = _space(undeclared)
    EventLog(space, "sharedhost", sign=False).append("item.create", {"id": "x", "title": "t"})


def test_an_explicitly_named_writer_is_not_a_guess(undeclared):
    space = _space(undeclared)
    EventLog(space, "nightshift", sign=False).append("item.create", {"id": "x", "title": "t"})


# --- L9: both remaining pushers run the publication fork gate --------------

def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _clone_with_pushed_log(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    work = tmp_path / "1-space"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false"),
                       ("core.hooksPath", str(work / ".git" / "hooks"))):
        _git(work, "config", key, value)
    (work / ".gitignore").write_text(".datacore/state/\n")
    log = EventLog(work, "w", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"e{i}", "title": "base"})
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base ledger")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "branch", "-M", "main")
    _git(work, "branch", "--set-upstream-to=origin/main", "main")
    _git(work, "remote", "set-head", "origin", "main")
    return origin, work


def _rewrite_log(work: Path, tmp_path: Path) -> str:
    """Rewind w.jsonl to two events and re-append: a valid chain, and a fork."""
    path = work / ".datacore/events/w.jsonl"
    keep = path.read_text().splitlines()[:2]
    alt = tmp_path / "alt"
    (alt / ".datacore/events").mkdir(parents=True)
    (alt / ".datacore/events/w.jsonl").write_text("\n".join(keep) + "\n")
    EventLog(alt, "w", sign=False).append("item.create", {"id": "forked", "title": "x"})
    path.write_text((alt / ".datacore/events/w.jsonl").read_text())
    _git(work, "commit", "-qam", "rewrite")
    return _git(work, "rev-parse", "HEAD")


def _origin_log(origin: Path) -> str:
    return _git(origin, "show", "main:.datacore/events/w.jsonl")


def test_transport_push_refuses_a_fork_and_names_the_recovery(tmp_path):
    import ledger_transport as lt
    origin, work = _clone_with_pushed_log(tmp_path)
    before = _origin_log(origin)
    _rewrite_log(work, tmp_path)
    result = lt._push_with_retry(work, "main")
    assert not result.ok
    assert "ledger fork" in result.reason
    assert "ledger_restore_prefix.py" in result.reason
    assert result.context["ledger_fork"]
    assert _origin_log(origin) == before


def test_transport_push_still_publishes_an_append(tmp_path):
    import ledger_transport as lt
    origin, work = _clone_with_pushed_log(tmp_path)
    EventLog(work, "w", sign=False).append("item.create", {"id": "e3", "title": "more"})
    _git(work, "commit", "-qam", "append")
    assert lt._push_with_retry(work, "main").ok
    assert len(_origin_log(origin).splitlines()) == 4


def test_knowledge_push_refuses_a_fork_and_names_the_recovery(tmp_path, monkeypatch):
    import knowledge_commit as kc
    import publication_history
    # Receipt verification is a separate gate with its own tests; bypass it so
    # the fork gate is what this exercises.
    monkeypatch.setattr(publication_history, "require_verified", lambda *a, **k: None)
    origin, work = _clone_with_pushed_log(tmp_path)
    before = _origin_log(origin)
    sha = _rewrite_log(work, tmp_path)
    with pytest.raises(kc.GitError, match="ledger fork") as exc:
        kc._push_commit(work, "main", sha)
    assert "ledger_restore_prefix.py" in str(exc.value)
    assert not exc.value.non_fast_forward
    assert _origin_log(origin) == before
    assert fork._index(before)  # origin still holds its three events


def test_knowledge_push_still_publishes_an_append(tmp_path, monkeypatch):
    import knowledge_commit as kc
    import publication_history
    monkeypatch.setattr(publication_history, "require_verified", lambda *a, **k: None)
    origin, work = _clone_with_pushed_log(tmp_path)
    EventLog(work, "w", sign=False).append("item.create", {"id": "e3", "title": "more"})
    _git(work, "commit", "-qam", "append")
    kc._push_commit(work, "main", _git(work, "rev-parse", "HEAD"))
    assert len(_origin_log(origin).splitlines()) == 4


def test_knowledge_integration_path_refuses_a_fork(tmp_path, monkeypatch):
    """Non-fast-forward: _push_converging integrates merge(origin, sha) and
    pushes THAT. The merged tree still carries the rewritten log."""
    import knowledge_commit as kc
    import publication_history
    monkeypatch.setattr(publication_history, "require_verified", lambda *a, **k: None)
    origin, work = _clone_with_pushed_log(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(other, "config", key, value)
    (other / "unrelated.md").write_text("another writer\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "unrelated")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/main")
    before = _origin_log(origin)
    sha = _rewrite_log(work, tmp_path)
    with pytest.raises(kc.LedgerForkRefused, match="ledger_restore_prefix.py"):
        kc._push_converging(work, "main", sha)
    assert _origin_log(origin) == before
