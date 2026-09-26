"""NS-9: A job that keeps failing goes to Miles as a repair pull request automatically.
I am told only if he cannot take it or gives up after three tries.

Kind: deterministic. Exercises the real chain against tmp fixtures:
  job_verify.main (a failing job, repeated with fresh artifacts)
    -> _dispatch_alert -> delegation (Telegram must stay silent)
  jobs.autofix.delegate (a box job becomes a PR-stage item addressed to miles)
  ledger_claim dispatcher in a scratch fleet (three failed attempts dead-letter
    the repair item) -> jobs.autofix.escalations (the owner is told).

Seeded failures (each must turn this red):
  - the delegation succeeds but job_verify still sends the Telegram alert;
  - a refused delegation is swallowed (no alert);
  - the dead-letter is not escalated (escalations ignores closed_kind 'dropped').
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import job_verify  # noqa: E402


# ── job_verify: a keeps-failing job is delegated, not paged ────────────────────

def _failing_setup(tmp_path, monkeypatch, delegate_result):
    import jobs.recurrence as R
    monkeypatch.setattr(R, "STATE", tmp_path / "rec.json")
    artifact = tmp_path / "out.log"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [{
        "name": "box-x", "machine": "box", "schedule": "0 3 * * *", "cmd": "true",
        "on_fail": "telegram",
        "artifacts": [{"path": str(artifact), "check": "regex", "arg": "^OK$"}]}]}))
    space = tmp_path / "space"; space.mkdir()
    monkeypatch.setenv("DATACORE_ACTOR", "test-actor")
    sent, delegated = [], []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(job_verify, "_file_task", lambda job, rec, failures: "org-task-1")
    monkeypatch.setattr(job_verify, "_close_task", lambda tid: True)
    monkeypatch.setattr(job_verify, "_delegate_repair",
                        lambda job, failures, rec: delegated.append(job.name) or delegate_result)
    argv = ["--machine", "box", "--manifest", str(manifest), "--space", str(space), "--alert", "telegram"]

    def run(i):
        artifact.write_text(f"FAILED run {i}\n")
        os.utime(artifact, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
        try:
            job_verify.main(argv)
        except SystemExit:
            pass
    return run, sent, delegated


def test_a_job_that_keeps_failing_is_handed_to_miles_and_the_owner_hears_nothing(tmp_path, monkeypatch):
    run, sent, delegated = _failing_setup(tmp_path, monkeypatch, ("delegated", "autofix-box-x -> miles"))
    for i in range(1, 6):                       # five real failures, well past DIP-0031's three
        run(i)
    assert delegated, "a failing job was never delegated"
    assert sent == [], f"the owner was paged while Miles had the repair: {sent}"


def test_a_repair_miles_cannot_take_reaches_the_owner(tmp_path, monkeypatch):
    run, sent, delegated = _failing_setup(
        tmp_path, monkeypatch, ("refused", "box-x's producer lives in plur-ai/plur"))
    run(1)
    assert delegated == ["box-x"]
    assert len(sent) == 1 and "box-x" in sent[0], "a refused delegation must reach the owner"


# ── autofix.delegate: the item is a pull request, addressed to miles ───────────

def test_the_repair_item_is_a_pull_request_for_miles(tmp_path, monkeypatch):
    import actor_identity
    import ledger.policy
    from jobs import autofix
    captured = {}
    monkeypatch.setattr(ledger.policy, "guarded_append", lambda log, kind, payload: captured.update(payload))
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    monkeypatch.setattr(autofix, "repo_for", lambda job, root: "datacore-one/datacore")
    roster = tmp_path / "infrastructure.yaml"
    roster.write_text("servers:\n"
                      "  winston: {manifest_machine: box, kind: server, ledger_actors: [winston], access: {actor: winston}}\n"
                      "  nightshift: {kind: server, ledger_actors: [nightshift, miles], access: {actor: miles}}\n")
    job = types.SimpleNamespace(name="box-x", machine="box", delegate=True, cmd="x", schedule="x")
    state, why = autofix.delegate(job, ["f"], {"first_failed": "2026-09-22"}, root=tmp_path, roster=roster)
    assert state == "delegated", why
    assert captured["assignee"] == "miles"
    assert "--stage merged" in captured["check"] and f"--item {captured['id']}" in captured["check"]
    assert "PR" in captured["body"] or "pull request" in captured["body"]


# ── three failed tries: dead-letter, then the owner is told ───────────────────

@pytest.fixture
def fleet(monkeypatch):
    """A scratch fleet whose global side effects are undone after the test."""
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="ns9-") as root:
            drill = DelegationDrill(root)
            space = drill.delegation_space("2-datacore")     # autofix's system space
            yield root, space, drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _repair_item(drill, space):
    return drill.delegate(space, by="winston", to="miles", id="autofix-box-x-20260926",
                          title="Repair box-x: failing on box since 2026-09-26",
                          check="true", autofix=True, job="box-x", machine="box", route="dev")


def test_three_failed_tries_dead_letter_and_reach_the_owner(fleet, monkeypatch):
    from jobs import autofix
    from ledger.log import EventLog
    from ledger_claim import MAX_ATTEMPTS
    root, space, drill = fleet
    monkeypatch.setattr(autofix, "_acked", lambda: set())
    iid = _repair_item(drill, space)
    log = EventLog(space, "miles", sign=False)
    for n in range(1, MAX_ATTEMPTS + 1):
        log.append("item.claim", {"id": iid, "owner": "miles"})
        log.append("item.release", {"id": iid, "owner": "miles", "reason": f"attempt {n} failed"})
        if n < MAX_ATTEMPTS:
            assert autofix.escalations(root) == [], f"the owner was told after only {n} tries"
    assert MAX_ATTEMPTS == 3, "the promise says three tries"

    out = drill.dispatch(space, "miles")
    assert "DEADLETTER" in out, out[:300]
    rows = autofix.escalations(root)
    assert len(rows) == 1 and "box-x" in rows[0] and "gave up" in rows[0], rows

    # ...and the escalation job's own contract turns red, which is what pages the owner.
    import re
    import subprocess
    jobs = {j["name"]: j for j in yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())["jobs"]}
    esc = jobs["box-autofix-escalation"]
    assert esc.get("delegate") is False and esc.get("on_fail") == "telegram", esc
    proc = subprocess.run([sys.executable, str(LIB / "jobs" / "autofix.py"), "--escalations", "--root", str(root)],
                          capture_output=True, text=True, timeout=120, env=dict(os.environ))
    last = (proc.stdout + proc.stderr).strip().splitlines()[-1]
    assert not re.search(esc["artifacts"][0]["arg"], last), f"escalation contract still green: {last!r}"
    assert "box-x" in proc.stdout
