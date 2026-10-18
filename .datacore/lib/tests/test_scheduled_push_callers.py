#!/usr/bin/env python3
"""Scheduled pushes, step 2: the callers that actually generate the traffic.

Step 1 put the policy in `ledger_transport.append()`, which no production
code calls. The pushes measured on 2026-10-18 come from the converge each
claim tick runs (ledger_claim_run.sh) and from knowledge commits (agent
wrap-up, recorded research publications). These tests hold, in both modes:

  * scheduled mode + agent: the claim tick RECEIVES (fetch and merge, through
    converge -- never a thinner `git pull`) but does not push;
  * scheduled mode + agent: a knowledge commit lands on the default branch
    locally and is not pushed; `urgent=True` still pushes;
  * immediate mode (the default): both push exactly as before;
  * agent_wrap_up and PublicationManifest pass the writer and the urgent flag.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
CLAIM_RUN = LIB / "ledger_claim_run.sh"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


def _clone(origin: Path, path: Path, who: str) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True)
    git(path, "config", "user.email", f"{who}@example.invalid")
    git(path, "config", "user.name", who)
    git(path, "config", "core.hooksPath", str(path / ".git" / "hooks"))
    return path


@pytest.fixture()
def fleet(tmp_path: Path, monkeypatch):
    """A data root with one registered knowledge space, its origin, a second
    clone standing in for another host, and declared disposable principals."""
    root = tmp_path / "Data"
    registry = root / ".datacore" / "registry"
    registry.mkdir(parents=True)
    (registry / "repositories.yaml").write_text(
        "repositories:\n  9-fixture:\n    category: knowledge\n")
    (registry / "principals.yaml").write_text(
        "principals:\n"
        "  owner: {kind: human, writes_as: [laptop]}\n"
        "  worker: {kind: agent, writes_as: [worker]}\n")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    seed = _clone(origin, tmp_path / "seed", "seed")
    (seed / "notes").mkdir()
    (seed / "notes" / "base.md").write_text("base\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "seed")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")

    space = _clone(origin, root / "9-fixture", "t")
    other = _clone(origin, tmp_path / "other", "o")

    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry / "principals.yaml")
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setenv("DATACORE_IDENTITY_FILE", str(tmp_path / "identity.env"))
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.delenv("DATACORE_PUSH_MODE", raising=False)
    return {"root": root, "space": space, "other": other, "origin": origin, "tmp": tmp_path}


def _remote_head(repo: Path) -> str:
    return git(repo, "ls-remote", "origin", "refs/heads/main").stdout.split()[0]


def _local_commit(space: Path, name: str = "local.md") -> str:
    (space / "notes" / name).write_text("agent work\n")
    git(space, "add", "--", f"notes/{name}")
    git(space, "commit", "-qm", "agent work", "--", f"notes/{name}")
    return git(space, "rev-parse", "HEAD").stdout.strip()


def _upstream_commit(other: Path) -> str:
    (other / "notes" / "theirs.md").write_text("another host\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "another host")
    git(other, "push", "-q", "origin", "HEAD:main")
    return git(other, "rev-parse", "HEAD").stdout.strip()


# ── 1. the claim tick (ledger_claim_run.sh) ────────────────────────────────

def _fake_lib_root(fleet) -> None:
    """The claim-run script calls $ROOT/.datacore/lib/<tool>. Give it the real
    transport and a ledger_claim.py that claims nothing, so the test exercises
    the two converges and not the executor."""
    lib = fleet["root"] / ".datacore" / "lib"
    lib.mkdir(parents=True)
    (lib / "ledger_transport.py").symlink_to(LIB / "ledger_transport.py")
    (lib / "ledger_claim.py").write_text("print('claimed nothing')\n")


def _run_tick(fleet, actor: str, mode: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DATACORE_PUSH_MODE"}
    if mode:
        env["DATACORE_PUSH_MODE"] = mode
    return subprocess.run(["bash", str(CLAIM_RUN), str(fleet["space"]), actor, "1"],
                          capture_output=True, text=True, timeout=120, env=env)


def test_claim_run_passes_its_actor_to_both_converges():
    lines = [l for l in CLAIM_RUN.read_text().splitlines()
             if "ledger_transport.py" in l and " converge" in l and not l.lstrip().startswith("#")]
    assert len(lines) == 2
    assert all('--actor "$ACTOR"' in l for l in lines)


def test_scheduled_agent_tick_receives_but_does_not_push(fleet):
    _fake_lib_root(fleet)
    space = fleet["space"]
    mine = _local_commit(space)
    theirs = _upstream_commit(fleet["other"])

    p = _run_tick(fleet, "worker", "scheduled")
    assert p.returncode == 0, p.stderr

    assert _remote_head(space) == theirs                                  # nothing pushed
    assert git(space, "merge-base", "--is-ancestor", theirs, "HEAD").returncode == 0  # received
    assert git(space, "merge-base", "--is-ancestor", mine, "HEAD").returncode == 0    # kept
    log = Path(os.environ["DATACORE_STATE"]) / "ledger-claim.log"
    assert "deferred" in log.read_text()


def test_immediate_agent_tick_still_publishes(fleet):
    _fake_lib_root(fleet)
    space = fleet["space"]
    mine = _local_commit(space)
    _upstream_commit(fleet["other"])

    p = _run_tick(fleet, "worker", None)
    assert p.returncode == 0, p.stderr
    remote = _remote_head(space)
    assert git(space, "merge-base", "--is-ancestor", mine, remote).returncode == 0


def test_scheduled_owner_converge_still_publishes(fleet, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    space = fleet["space"]
    mine = _local_commit(space)
    res = lt.converge(space, root=fleet["root"], actor="laptop")
    assert res.ok, res.reason
    assert _remote_head(space) == mine


def test_converge_without_an_actor_is_unchanged(fleet, monkeypatch):
    """The slot job and every other caller name no actor: they publish."""
    import ledger_transport as lt
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    space = fleet["space"]
    mine = _local_commit(space)
    assert lt.converge(space, root=fleet["root"]).ok
    assert _remote_head(space) == mine


# ── 2. knowledge commits ───────────────────────────────────────────────────

def test_scheduled_agent_knowledge_commit_is_not_pushed(fleet, monkeypatch):
    import knowledge_commit as kc
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    space = fleet["space"]
    before = _remote_head(space)
    (space / "notes" / "report.md").write_text("findings\n")
    r = kc.commit_knowledge(space, ["notes/report.md"], "agent report", actor="worker")
    assert r["commit"] and r["pushed"] is False
    assert git(space, "show", "main:notes/report.md").stdout == "findings\n"
    assert _remote_head(space) == before


def test_scheduled_agent_urgent_knowledge_commit_is_pushed(fleet, monkeypatch):
    import knowledge_commit as kc
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    space = fleet["space"]
    (space / "notes" / "briefing.md").write_text("for the owner\n")
    r = kc.commit_knowledge(space, ["notes/briefing.md"], "briefing", actor="worker", urgent=True)
    assert r["pushed"] is True
    assert _remote_head(space) == r["commit"]


def test_immediate_agent_knowledge_commit_is_pushed(fleet):
    import knowledge_commit as kc
    space = fleet["space"]
    (space / "notes" / "report.md").write_text("findings\n")
    r = kc.commit_knowledge(space, ["notes/report.md"], "agent report", actor="worker")
    assert r["pushed"] is True
    assert _remote_head(space) == r["commit"]


def test_explicit_push_false_still_wins(fleet, monkeypatch):
    import knowledge_commit as kc
    space = fleet["space"]
    before = _remote_head(space)
    (space / "notes" / "report.md").write_text("findings\n")
    r = kc.commit_knowledge(space, ["notes/report.md"], "draft", push=False,
                            actor="laptop", urgent=True)
    assert r["pushed"] is False and _remote_head(space) == before


def test_scheduled_manifest_publication_by_an_agent_is_not_pushed(fleet, monkeypatch):
    from publication_manifest import PublicationManifest
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    space = fleet["space"]
    before = _remote_head(space)
    m = PublicationManifest(space)
    out = space / "notes" / "research.md"
    out.write_text("research\n")
    m.record(out, "research\n")
    assert m.publish("research output", actor="worker")
    assert git(space, "show", "main:notes/research.md").stdout == "research\n"
    assert _remote_head(space) == before


def test_manifest_defaults_to_this_hosts_actor(fleet, monkeypatch):
    """research_orchestrator calls publish() with no actor: the host's declared
    writer decides, so an agent host defers and the owner's machine does not."""
    from publication_manifest import PublicationManifest
    monkeypatch.setenv("DATACORE_PUSH_MODE", "scheduled")
    monkeypatch.setenv("DATACORE_ACTOR", "worker")
    space = fleet["space"]
    before = _remote_head(space)
    m = PublicationManifest(space)
    out = space / "notes" / "research.md"
    out.write_text("research\n")
    m.record(out, "research\n")
    assert m.publish("research output")
    assert _remote_head(space) == before


def test_agent_wrap_up_passes_its_agent_and_urgent_flag(fleet, monkeypatch):
    import agent_wrap_up as awu
    seen = []

    def fake(space, paths, msg, push=True, *, actor=None, urgent=False):
        seen.append({"actor": actor, "urgent": urgent, "push": push})
        return {"commit": "abc"}

    monkeypatch.setattr(awu, "commit_knowledge", fake)
    (fleet["space"] / "notes" / "work.md").write_text("w\n")
    awu.wrap_up(fleet["root"], "worker", "tick", "did work", {})
    awu.wrap_up(fleet["root"], "worker", "tick", "did more", {}, urgent=True)
    assert [s["actor"] for s in seen] == ["worker", "worker"]
    assert [s["urgent"] for s in seen] == [False, True]


def test_agent_wrap_up_cli_has_an_urgent_flag():
    p = subprocess.run([sys.executable, str(LIB / "agent_wrap_up.py"), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert "--urgent" in p.stdout


# ── 3. a policy answer for shell callers ───────────────────────────────────

@pytest.mark.parametrize("mode, actor, urgent, expected", [
    (None, "worker", False, "now"),
    ("scheduled", "worker", False, "deferred"),
    ("scheduled", "worker", True, "now"),
    ("scheduled", "laptop", False, "now"),
])
def test_cli_policy_answers_now_or_deferred(fleet, mode, actor, urgent, expected):
    """So a shell script (ledger-claim-pull.sh) can guard its push in one line."""
    env = {k: v for k, v in os.environ.items() if k != "DATACORE_PUSH_MODE"}
    if mode:
        env["DATACORE_PUSH_MODE"] = mode
    p = subprocess.run([sys.executable, str(LIB / "ledger_transport.py"), "policy",
                        "--actor", actor, *(["--urgent"] if urgent else [])],
                       capture_output=True, text=True, timeout=30, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == expected
