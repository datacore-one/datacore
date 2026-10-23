"""The repair loop has a breaker across days, knows why a job failed, and learns.

Measured 2026-10-09..23 (automation review, 2-datacore ops report 2026-10-23):
676 failed repair attempts against about 43 successes. Each failing job got a
FRESH repair item every day (`autofix-<job>-<date>`), so the three-attempt
dead-letter bounded one item and never the job: the same 15 jobs failed about
three times a day for up to 30 days. On 21 Oct about 60 attempts failed on the
Claude weekly usage limit and were counted as failed repairs. Host-state faults
(a store over its size ceiling, a checkout behind upstream) were handed to an
agent that could not fix them from a fresh copy. And the agent was told only
the item title -- "Repair X: failing on box since ..." -- never the error.

This pins the five things the task's DONE_WHEN names:

  1. a job that fails three repair attempts across days gets no new repair
     items until its definition or code changes (fingerprint), or it passes;
  2. a usage-limit failure pauses repairs and is not counted as a code fault;
  3. a host-state failure is escalated to the owner once, not delegated;
  4. the repairer's prompt carries the job's real error text;
  5. every repair outcome is recorded as a learning candidate.
"""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

#: The line the dispatcher journalled ~60 times on 2026-10-21, as truncated.
USAGE_LINE = ("claude-code: claude reported error: api_error_status=429; "
              "terminal_reason=completed; You've hit your weekly li")


def _job(root: Path, name="box-x", machine="box", artifact=None, **extra):
    arts = [types.SimpleNamespace(path=str(artifact), max_age_hours=None)] if artifact else []
    return types.SimpleNamespace(name=name, machine=machine, delegate=True,
                                 cmd="~/Data/.datacore/lib/producer.sh", schedule="0 * * * *",
                                 artifacts=arts, **extra)


@pytest.fixture
def unit(tmp_path, monkeypatch):
    """autofix against a real ledger in a scratch root; item.create captured."""
    from jobs import autofix
    import ledger.policy
    import actor_identity
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    appended = []
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: appended.append((kind, payload)))
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    roster = tmp_path / "infrastructure.yaml"
    roster.write_text("servers:\n"
                      "  winston: {kind: server, manifest_machine: box, ledger_actors: [winston], "
                      "access: {actor: winston}}\n")
    space = tmp_path / "2-datacore"
    (space / ".datacore" / "events").mkdir(parents=True)
    producer = tmp_path / ".datacore" / "lib" / "producer.sh"
    producer.parent.mkdir(parents=True)
    producer.write_text("#!/bin/sh\necho v1\n")
    return types.SimpleNamespace(autofix=autofix, root=tmp_path, space=space, roster=roster,
                                 appended=appended, producer=producer)


def _failed_repair(u, iid: str, fp: str, *, kind: str | None = None, job="box-x"):
    """One past repair item, claimed and released once by its assignee."""
    from ledger.log import EventLog
    EventLog(u.space, "winston").append("item.create", {
        "id": iid, "title": f"Repair {job}", "assignee": "winston", "autofix": True,
        "job": job, "machine": "box", "contract_sha": "abc", "fingerprint": fp})
    EventLog(u.space, "winston").append("item.claim", {"id": iid, "owner": "winston"})
    rel = {"id": iid, "owner": "winston", "error": "check failed: still red"}
    if kind:
        rel["kind"] = kind
        rel["error"] = USAGE_LINE
    EventLog(u.space, "winston").append("item.release", rel)


def _delegate(u, job, failures=("box-x.log: regex '^OK$' did not match",)):
    return u.autofix.delegate(job, list(failures), {"first_failed": "2026-10-01", "consecutive": 3},
                              root=u.root, assignee="winston", roster=u.roster)


# ── 1. the breaker: three failed attempts across days, then no new items ──────

def test_three_failed_repairs_across_days_stop_new_repair_items(unit):
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261020", "20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp)

    state, why = _delegate(unit, job)

    assert [k for k, _ in unit.appended] == [], (
        f"a fourth day's repair item was filed for a job whose last three repairs failed: {why}")
    assert state == "escalate", (state, why)
    assert "3" in why and "until" in why, why


def test_two_failed_repairs_still_delegate(unit):
    """Control: the breaker is not a hair trigger."""
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp)
    state, why = _delegate(unit, job)
    assert state == "delegated", why
    assert unit.appended[0][1].get("fingerprint") == fp, "the repair item does not carry the fingerprint"


def test_a_change_to_the_producer_reopens_the_breaker(unit):
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261020", "20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp)
    assert _delegate(unit, job)[0] == "escalate"

    unit.producer.write_text("#!/bin/sh\necho v2 fixed\n")

    assert unit.autofix.fingerprint(job, unit.root) != fp
    state, why = _delegate(unit, job)
    assert state == "delegated", f"the job's code changed but the breaker stayed open: {why}"


def test_a_change_to_the_contract_reopens_the_breaker(unit, monkeypatch):
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261020", "20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp)
    monkeypatch.setattr(unit.autofix, "contract_sha", lambda name, manifest: "def")
    assert _delegate(unit, job)[0] == "delegated"


def test_a_pass_resets_the_breaker(unit):
    from ledger.log import EventLog
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261020", "20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp)
    EventLog(unit.space, "winston").append("metric.attest", {
        "metric": "job.verify", "job": "box-x", "ok": True, "failures": []})
    state, why = _delegate(unit, job)
    assert state == "delegated", f"the job passed after its failed repairs, yet stayed broken: {why}"


# ── 2. a usage limit pauses repairs and is not a code fault ───────────────────

def test_the_weekly_limit_line_is_a_usage_limit_not_a_code_fault():
    from jobs import autofix
    import claim_backoff
    assert claim_backoff.is_usage_limit(USAGE_LINE)
    assert not claim_backoff.is_usage_limit("rate limit exceeded, retry in 20s")
    assert autofix.classify_failure(USAGE_LINE) == "usage_limit"


def test_usage_limit_releases_do_not_count_towards_the_breaker(unit):
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261020", "20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp, kind="usage_limit")
    # ...but the most recent one is a fresh usage limit, so repairs are paused:
    state, why = _delegate(unit, job)
    assert state == "paused", (state, why)
    assert unit.appended == []
    assert "usage limit" in why

    b = unit.autofix.breaker(unit.root, "box-x", fp, now_ms=time.time() * 1000 + 7 * 3600_000)
    assert b["attempts"] == 0, f"usage-limit releases were counted as failed repairs: {b}"
    assert not b["open"] and not b["usage_pause"], b


def test_the_dispatcher_pauses_on_a_usage_limit_and_keeps_the_repair(fleet, monkeypatch):  # noqa: F811
    import ledger_claim
    import claim_backoff
    from jobs import autofix
    from ledger_claim import MAX_ATTEMPTS
    root, space, drill = fleet
    calls = []

    def weekly_limit(title, route, cwd, item_id="", hops=0, actor=""):
        calls.append(item_id)
        return False, USAGE_LINE, {}
    monkeypatch.setattr(ledger_claim, "run_task", weekly_limit)
    a = drill.delegate(space, by="winston", to="miles", id="autofix-box-x-20261021",
                       title="Repair box-x", check="test -f a.txt", autofix=True, job="box-x",
                       machine="box", fingerprint="f1")
    b = drill.delegate(space, by="winston", to="miles", id="autofix-box-y-20261021",
                       title="Repair box-y", check="test -f b.txt", autofix=True, job="box-y",
                       machine="box", fingerprint="f2")

    first = drill.dispatch(space, "miles")
    assert len(calls) == 1, f"kept spending the budget after a usage limit: {first[:400]}"
    later = [drill.dispatch(space, "miles") for _ in range(MAX_ATTEMPTS + 2)]
    assert len(calls) == 1, "a host on its usage limit claimed again before the probe interval"
    for iid in (a, b):
        assert drill.item(space, iid).status != "dismissed", "a usage limit dead-lettered a repair"
    rel = [e for e in _events(space, a, "item.release")]
    assert rel and all((e.payload or {}).get("kind") == "usage_limit" for e in rel), \
        [e.payload for e in rel]
    assert autofix.breaker(root, "box-x", "f1")["usage_pause"], "delegate() would keep filing repairs"
    claim_backoff.clear("miles")


# ── 3. a host-state failure goes to the owner, once ───────────────────────────

@pytest.mark.parametrize("failure", [
    "~/Data/2-datacore: repo is 4 commit(s) behind upstream -- unverifiable",
    "~/.datacore/state/lens.log: last line does not match '^OK$' (said: store 2.24 GB over its 2 GB size ceiling)",
    "~/.datacore/state/x.log: OSError: [Errno 28] No space left on device",
])
def test_host_state_failures_are_classified(failure):
    from jobs import autofix
    assert autofix.classify_failure(failure) == "host_state"


def test_code_faults_and_declared_host_jobs():
    from jobs import autofix
    assert autofix.classify_failure("x.log: regex '^OK$' did not match (said: Traceback ...)") == "code_fault"
    assert autofix.classify_failure("x.log: regex did not match",
                                    types.SimpleNamespace(repair="host")) == "host_state"


def test_the_manifest_can_declare_a_host_job(tmp_path):
    from jobs.manifest import ManifestError, load_manifest
    base = {"name": "d", "machine": "mac", "schedule": "0 * * * *", "cmd": "true",
            "artifacts": [{"path": "/tmp/d.log"}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "jobs": [{**base, "repair": "host"}]}))
    assert load_manifest(p)[0].repair == "host"
    p.write_text(yaml.safe_dump({"version": 1, "jobs": [base]}))
    assert load_manifest(p)[0].repair == "agent"
    p.write_text(yaml.safe_dump({"version": 1, "jobs": [{**base, "repair": "sometimes"}]}))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_a_host_state_failure_is_escalated_once_and_never_delegated(unit, monkeypatch, tmp_path):
    import job_verify as jv
    from jobs import recurrence
    monkeypatch.setattr(jv, "_NO_EMIT", False)
    monkeypatch.setattr(jv, "DATACORE_ROOT", unit.root)
    monkeypatch.setattr(jv, "_file_task", lambda job, rec, failures: None)
    monkeypatch.setattr(recurrence, "should_alert", lambda rec: True)   # every run a new day
    import roster as _roster
    monkeypatch.setattr(_roster, "person_on", lambda machine: None)
    monkeypatch.setattr(_roster, "resident_agent", lambda machine: "winston")
    monkeypatch.setattr(unit.autofix, "ROSTER", unit.roster)
    sent = []
    monkeypatch.setattr(jv, "_send_telegram", lambda message: sent.append(message) or True)
    log = tmp_path / "box-x.log"
    failure = f"{log}: repo is 4 commit(s) behind upstream -- unverifiable"
    for n in range(4):
        log.write_text(f"run {n}\n")                        # a new artifact each run
        os.utime(log, (time.time() + n * 3600, time.time() + n * 3600))
        jv._dispatch_alert("telegram", "box-x", [failure], job=_job(unit.root, artifact=log))

    assert [k for k, _ in unit.appended] == [], "a host-state fault was handed to an agent"
    assert len(sent) == 1, f"escalated {len(sent)} times, not once: {sent}"
    assert "owner" in sent[0].lower() or "host" in sent[0].lower(), sent[0]
    assert "behind upstream" in sent[0], "the escalation does not carry the real failure"


# ── 4. the repairer is told the real error ────────────────────────────────────

def test_the_repair_body_carries_the_jobs_own_output(unit, tmp_path):
    log = tmp_path / "box-x.log"
    log.write_text("starting\nTraceback (most recent call last):\nKeyError: 'lane'\n")
    state, why = _delegate(unit, _job(unit.root, artifact=log),
                           failures=(f"{log}: last line does not match '^OK$'",))
    assert state == "delegated", why
    body = unit.appended[0][1]["body"]
    assert "KeyError: 'lane'" in body, f"the repair item omits the job's own error: {body}"


def test_the_repairer_prompt_contains_the_items_error_text(fleet, monkeypatch):  # noqa: F811
    import ledger_claim
    root, space, drill = fleet
    prompts = []

    def capture(title, route, cwd, item_id="", hops=0, actor=""):
        prompts.append(title)
        return False, "check failed: nothing done", {}
    monkeypatch.setattr(ledger_claim, "run_task", capture)
    drill.delegate(space, by="winston", to="miles", id="autofix-box-x-20261023",
                   title="Repair box-x: failing on box since 2026-10-01", check="test -f k.txt",
                   autofix=True, job="box-x", machine="box",
                   body="box-x.log said:\nKeyError: 'lane' in heartbeat.py line 88")
    drill.dispatch(space, "miles")
    assert prompts, "the repair was never dispatched"
    assert "KeyError: 'lane'" in prompts[0], (
        f"the repairer was told only the title, not the error: {prompts[0]!r}")


# ── 5. every repair outcome is a learning candidate ───────────────────────────

def _candidates(root: Path) -> list[dict]:
    p = root / ".datacore" / "state" / "learning_candidates.yaml"
    return (yaml.safe_load(p.read_text()) or []) if p.exists() else []


def test_each_repair_outcome_is_recorded_as_a_learning_candidate(fleet, monkeypatch):  # noqa: F811
    import ledger_claim
    import claim_backoff
    root, space, drill = fleet

    def did_it(title, route, cwd, item_id="", hops=0, actor=""):
        if "box-ok" in item_id:
            (Path(cwd) / "ok.txt").write_text("ok\n")
        return True, "fixed the producer", {}
    monkeypatch.setattr(ledger_claim, "run_task", did_it)
    drill.delegate(space, by="winston", to="miles", id="autofix-box-ok-20261023",
                   title="Repair box-ok", check="test -f ok.txt", autofix=True, job="box-ok",
                   machine="box", fingerprint="fa")
    drill.delegate(space, by="winston", to="miles", id="autofix-box-no-20261023",
                   title="Repair box-no", check="test -f never.txt", autofix=True, job="box-no",
                   machine="box", fingerprint="fb")
    drill.dispatch(space, "miles")

    monkeypatch.setattr(ledger_claim, "run_task", lambda *a, **k: (False, USAGE_LINE, {}))
    drill.delegate(space, by="winston", to="miles", id="autofix-box-lim-20261023",
                   title="Repair box-lim", check="test -f l.txt", autofix=True, job="box-lim",
                   machine="box", fingerprint="fc")
    drill.dispatch(space, "miles")
    claim_backoff.clear("miles")

    got = {c.get("job"): c for c in _candidates(root) if c.get("source") == "autofix"}
    assert set(got) >= {"box-ok", "box-no", "box-lim"}, f"outcomes not recorded: {list(got)}"
    assert got["box-ok"]["outcome"] == "fixed"
    assert got["box-no"]["outcome"] == "failed" and got["box-no"]["failure_class"] == "code_fault"
    assert got["box-lim"]["outcome"] == "paused" and got["box-lim"]["failure_class"] == "usage_limit"
    for c in got.values():
        assert c["status"] == "pending" and c["text"] and c["fingerprint"], c


from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet, _events  # noqa: E402,F401


# ── 6. the owner can reset a tripped breaker (owner decision, 2026-10-23) ─────

def test_an_owner_reset_reopens_the_breaker_and_is_recorded(unit):
    """A breaker otherwise reopens only on a change or a pass. After the owner
    fixes the host by hand, one reset gives the job a fresh three attempts; the
    reset is a ledger record, so every host sees it."""
    from ledger.log import read_events
    job = _job(unit.root)
    fp = unit.autofix.fingerprint(job, unit.root)
    for day in ("20261020", "20261021", "20261022"):
        _failed_repair(unit, f"autofix-box-x-{day}", fp)
    assert _delegate(unit, job)[0] == "escalate"

    unit.autofix.reset_breaker(unit.root, "box-x", reason="host fixed by hand", actor="winston")

    assert any((e.payload or {}).get("metric") == "job.breaker_reset"
               and (e.payload or {}).get("job") == "box-x" for e in read_events(unit.space))
    state, why = _delegate(unit, job)
    assert state == "delegated", f"the owner reset the breaker but it stayed open: {why}"
    assert unit.autofix.breaker(unit.root, "box-x", fp)["attempts"] == 0
