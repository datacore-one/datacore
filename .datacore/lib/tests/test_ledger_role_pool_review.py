"""Role pools: DONE_WHEN, the pinned role template, submitted-for-review and an
independent check (DIP-0034 October amendment, section G; Codex audit
2026-10-18, finding 14).

Finding 14: pool budgets and independent completion were promises without a
contract. The minimum this file pins down, all on the PRODUCTION eligibility
path (no `allow_any` hook; the hub decides with `pool_rule` and the policy
inputs read from the tmp installation's principal registry):

  1. the grant refuses a pool task without a DONE_WHEN;
  2. the lease pins the content hash of the role template it names, so a
     later edit of the template cannot silently redefine the attempt's work,
     and the holder refuses to load a template that no longer matches;
  3. the holder's completion of a pool task means SUBMITTED FOR REVIEW: it
     records a digest of the result and evidence (status `submitted`, org
     REVIEW), and it is not accepted;
  4. it becomes done (`verified`) only after a check by a different EXECUTION
     IDENTITY: another principal in the registry, not merely another writer
     name of the holder's principal, bound to the attempt and the digest it
     reviewed; at the front door, in the writer, and on replay from git;
  5. the daily budget is a stop-on-recorded-spend threshold, not a hard cap:
     work in flight can cross it, and only new leases stop.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_ledger_role_pool as pool  # noqa: E402
from test_ledger_hub import APPROVER as TEST_APPROVER, git_pull  # noqa: E402
from test_ledger_role_pool import DONE, PoolEnv, pool_item, refused, submit_raw  # noqa: E402

from ledger import hub as hubmod
from ledger.events import canonical_bytes
from ledger.fold import fold, org_task_state, pool_accepted
from ledger.log import read_events

#: As the pool tests' registry, plus a second writer name (`wa2`) for the
#: agent principal `wa`: the same execution identity under another name.
PRINCIPALS = """\
version: 1
principals:
  owner:
    kind: human
    writes_as: [own]
  wa:
    kind: agent
    writes_as: [wa, wa2]
  wb:
    kind: agent
  wc:
    kind: agent
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pool, "PRINCIPALS", PRINCIPALS)
    e = PoolEnv(tmp_path, monkeypatch)
    yield e
    e.close()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def result_digest(payload: dict) -> str:
    return sha(canonical_bytes({"result": payload.get("result"), "evidence": payload.get("evidence")}))


def hub_item(hub, iid: str):
    """The hub's AUTHORITATIVE view (admission, lease grantors and policy)."""
    return hub._state()[1].items[iid]


# ── 1. DONE_WHEN is checked when the lease is granted ──────────────────────

def test_f14_the_grant_refuses_a_pool_task_without_done_when_on_the_production_path(env):
    hub = env.start()
    assert hub.eligibility is None                      # the production rule, not a test hook
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "blank", done_when="   ")
    err = refused(wa.request_lease, "blank")
    assert err.code == "not_eligible" and "DONE_WHEN" in err.message
    assert hub_item(hub, "blank").lease_holder is None


# ── 2. the role template's content hash is pinned to the attempt ────────────

def test_f14_the_lease_pins_the_role_templates_content_hash_to_the_attempt(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    template = env.roles_dir / "researcher.md"
    first = sha(template.read_bytes())
    pool_item(own, "p1")

    grant = wa.request_lease("p1")
    role = grant["lease"]["payload"]["role"]
    assert role["sha256"] == first and role["template"] == pool.RESEARCHER
    item = hub_item(hub, "p1")
    assert item.lease_ref["role"]["sha256"] == first                     # in the folded attempt
    assert wa.load_role("p1") == template.read_text()                    # what the holder executes

    # The template changes while the attempt runs: the attempt keeps its pin,
    # and the holder refuses to load instructions its lease did not name.
    template.write_text("# researcher\n\nRewritten instructions.\n")
    assert hub_item(hub, "p1").lease_ref["role"]["sha256"] == first
    assert refused(wa.load_role, "p1").code == "role_changed"

    # A new attempt pins the template as it is now.
    wa.append("item.release", {"id": "p1"})
    regrant = wb.request_lease("p1")
    assert regrant["lease"]["payload"]["role"]["sha256"] == sha(template.read_bytes()) != first
    assert hub_item(hub, "p1").lease_ref["role"]["sha256"] != first


# ── 3. completion means "submitted for review" ──────────────────────────────

def test_f14_a_pool_completion_is_submitted_for_review_with_a_digest_of_result_and_evidence(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    pinned = sha((env.roles_dir / "researcher.md").read_bytes())
    pool_item(own, "p1")
    pool_item(own, "p2")
    attempt = wa.request_lease("p1")["lease"]["payload"]["attempt"]
    done = {"id": "p1", "result": "three competitors, summarised", "evidence": ["https://example.com/a"]}
    wa.append("item.complete", done)

    item = hub_item(hub, "p1")
    assert item.status == "submitted", item.history
    assert org_task_state(item) == "REVIEW" and not pool_accepted(item)
    assert item.submission == {"attempt": attempt, "task_hash": item.lease_ref["task_hash"],
                               "result_digest": result_digest(done), "role_sha256": pinned, "holder": "wa"}
    # Submitted work is not open again for another agent.
    assert refused(wb.request_lease, "p1").code == "closed"

    # A completion whose digest does not match its own result is not a submission.
    wa.request_lease("p2")
    err = refused(submit_raw, hub, wa, "item.complete",
                  {"id": "p2", "result": "x", "evidence": [], "result_digest": "0" * 64})
    assert err.code == "not_authorized" and "digest" in err.message
    assert hub_item(hub, "p2").status == "claimed"


# ── 4. done only after an independent execution identity checked it ────────

def test_f14_only_another_execution_identity_accepts_the_submission(env):
    hub = env.start()
    own, wa, wa2, wb, wz = (env.writer(a) for a in ("own", "wa", "wa2", "wb", "wz"))
    pool_item(own, "p1")
    wa.request_lease("p1")
    wa.append("item.complete", {"id": "p1", "result": "summary", "evidence": ["notes.md"]})
    for w in (wa2, wb, wz):
        git_pull(env.hub_dir, w.space_dir)                               # every reviewer reads the work

    # Another writer name of the holder's own principal is not independent:
    # refused by the writer before anything is written ...
    before = wa2.log.path.read_bytes() if wa2.log.path.exists() else b""
    err = refused(wa2.append, "item.verify", {"id": "p1", "check": DONE})
    assert err.code == "not_independent", err
    assert (wa2.log.path.read_bytes() if wa2.log.path.exists() else b"") == before
    # ... and at the hub's front door when it arrives anyway.
    err = refused(submit_raw, hub, wa2, "item.verify", {"id": "p1", "check": DONE, **_binding(hub, "p1")})
    assert err.code == "not_authorized" and "independent" in err.message
    # A writer that belongs to no principal has no execution identity to compare.
    err = refused(submit_raw, hub, wz, "item.verify", {"id": "p1", "check": DONE, **_binding(hub, "p1")})
    assert err.code == "not_authorized" and "independent" in err.message
    # A check bound to other content than was submitted reviews nothing.
    err = refused(submit_raw, hub, wb, "item.verify",
                  {"id": "p1", "check": DONE, **_binding(hub, "p1"), "result_digest": "f" * 64})
    assert err.code == "not_authorized" and "digest" in err.message
    assert hub_item(hub, "p1").status == "submitted"

    # A different agent principal's check, bound to the attempt and digest: accepted.
    # (wb's refused check above stays in its own log as a rejected proposal.)
    wb.append("item.verify", {"id": "p1", "check": DONE})
    item = hub_item(hub, "p1")
    assert item.status == "verified" and pool_accepted(item) and org_task_state(item) == "DONE"


def test_f14_a_same_identity_check_imported_by_git_is_a_no_op_on_replay(env):
    hub = env.start()
    own, wa, wa2 = env.writer("own"), env.writer("wa"), env.writer("wa2")
    pool_item(own, "p1")
    wa.request_lease("p1")
    wa.append("item.complete", {"id": "p1", "result": "summary", "evidence": []})
    git_pull(env.hub_dir, wa2.space_dir)
    binding = _binding(hub, "p1")
    wa2.log.append("item.verify", {"id": "p1", "check": DONE, **binding})   # never went through the hub
    copy = env.tmp / "copy"
    (copy / ".datacore" / "events").mkdir(parents=True)
    git_pull(env.hub_dir, copy)
    (copy / ".datacore" / "events" / wa2.log.path.name).write_bytes(wa2.log.path.read_bytes())
    item = fold(read_events(copy), policy=env.policy).items["p1"]
    assert item.status == "submitted" and not pool_accepted(item), item.history
    # Without the registry (a plain fold) the different writer NAME is all the
    # fold can see; the independent-identity rule needs the policy inputs.
    assert hub_item(hub, "p1").status == "submitted"


def _binding(hub, iid: str) -> dict:
    sub = hub_item(hub, iid).submission
    return {"attempt": sub["attempt"], "task_hash": sub["task_hash"], "result_digest": sub["result_digest"]}


# ── 5. the budget is a stop-on-recorded-spend threshold ─────────────────────

def test_f14_the_budget_stops_new_leases_on_recorded_spend_and_is_not_a_hard_cap(env):
    """Two leases granted while nothing was spent may together spend past the
    day's threshold: the hub cannot stop work in flight, it only refuses new
    leases once recorded spend reaches the threshold (amendment G)."""
    hub = env.start()
    own, wa, wb, wc = env.writer("own"), env.writer("wa"), env.writer("wb"), env.writer("wc")
    hub.set_budget("researcher", 500)
    for i in range(3):
        pool_item(own, f"p{i}")
    wa.request_lease("p0")
    wb.request_lease("p1")
    wa.append("spend.record", {"item": "p0", "cents": 400, "model": "test"})
    wb.append("spend.record", {"item": "p1", "cents": 400, "model": "test"})
    state = hub._state()[1]
    assert state.pool_spend == {"researcher|2027-01-15": 800}                # past the threshold, recorded
    assert refused(wc.request_lease, "p2").code == "budget_reached"
    wa.append("item.complete", {"id": "p0", "result": "r", "evidence": []})  # work in flight still submits
    assert hub_item(hub, "p0").status == "submitted"


def test_f14_a_submitted_pool_item_stays_in_the_org_projection_as_review():
    """Added with the code: a submission awaiting review is live work, so the
    projector keeps it (as REVIEW) and the checkpoint renders it the same way."""
    from ledger.projector import LIVE_STATUSES
    assert "submitted" in LIVE_STATUSES


# ── Codex re-audit of 2026-10-18 (report: 2-datacore/1-tracks/engineering/reports/
#    2026-10-18-ledger-hub-codex-reaudit-report.md), new defects 5 and 6 ─────

def test_reaudit_new5_a_live_attempt_keeps_its_class_role_and_review_whatever_the_task_says_later(env):
    """Codex: a pool holder set itself as the assignee and completed without
    independent review. The attempt pins what it was granted as."""
    from ledger.log import EventLog
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "p1")
    lease = wa.request_lease("p1")["lease"]["payload"]
    assert lease["class"] == "pool" and lease["review"] is True, lease
    # The holder cannot make its pool attempt addressed work, change its role
    # or drop its DONE_WHEN while the attempt is live: over the network ...
    for change in ({"assignee": "wa"}, {"role": "scout"}, {"done_when": ""}):
        err = refused(wa.append, "item.update", {"id": "p1", **change})
        assert err.code == "not_authorized", (change, err)
    # ... nor by git.
    EventLog(env.hub_dir, "wb", keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "item.update", {"id": "p1", "assignee": "wb"})
    assert "assignee" not in hub_item(hub, "p1").payload
    # Even when the owner's approver re-addresses the task, the live attempt
    # keeps the obligations it was granted with: its completion is a submission.
    boss = env.writer(TEST_APPROVER)
    boss.append("item.update", {"id": "p1", "assignee": "wa"})
    assert hub_item(hub, "p1").payload["assignee"] == "wa"
    wa.append("item.complete", {"id": "p1", "result": "summary", "evidence": []})
    item = hub_item(hub, "p1")
    assert item.status == "submitted" and not pool_accepted(item), item.history


def test_reaudit_new6_pool_preparation_reads_the_authoritative_state(env):
    """Codex: the writer prepared a pool check from a plain fold, so a lease
    nobody granted, published by git, decided what the check was bound to."""
    from ledger.log import EventLog
    env.start()
    own, wb = env.writer("own"), env.writer("wb")
    pool_item(own, "p1")
    forger = EventLog(env.hub_dir, "wd", keys_dir=env.keys_dir, registry_path=env.registry, sign=True)
    forger.append("item.lease", {"id": "p1", "holder": "wd", "attempt": "forged-attempt", "task_hash": "x",
                                 "granted_ms": env.clock(), "expires_ms": env.clock() + 900_000})
    forger.append("item.complete", {"id": "p1", "attempt": "forged-attempt", "task_hash": "x",
                                    "result": "made up", "evidence": []})
    git_pull(env.hub_dir, wb.space_dir)
    assert fold(read_events(wb.space_dir)).items["p1"].submission is not None      # the bypass
    prepared = wb._pool_prepare("item.verify", {"id": "p1", "check": DONE})
    assert prepared.get("attempt") != "forged-attempt", prepared


# ── Codex round-3 verdict (report: 2-datacore/1-tracks/engineering/reports/
#    2026-10-19-ledger-hub-round3-codex-verdict.md), defect 6 and Follow-up 5 ─
#
# Once a task has been pool work (created as a pool task, or leased as one),
# it stays pool work for review: any completion goes through submitted ->
# independent check -> verified, whoever it is addressed to later. An agent
# cannot turn pool work into addressed work for itself.

def test_round3_pool_release_reclassify_is_refused_and_the_next_lease_stays_pool_work(env):
    """Codex `pool_release_reclassify`: wa leased a pool task, released it,
    set itself as the assignee, got an `addressed` lease and completed with no
    review submission."""
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "t")
    assert wa.request_lease("t")["lease"]["payload"]["class"] == "pool"
    wa.append("item.release", {"id": "t"})
    # The agent cannot address the pool task to itself ...
    err = refused(wa.append, "item.update", {"id": "t", "assignee": "wa"})
    assert err.code == "not_authorized", err
    # ... nor slip the edit in by git.
    from ledger.log import EventLog
    EventLog(env.hub_dir, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "item.update", {"id": "t", "assignee": "wa"})
    assert "assignee" not in hub_item(hub, "t").payload
    # Its next lease is pool work again, and its completion is a submission.
    lease = wa.request_lease("t")["lease"]["payload"]
    assert lease["class"] == "pool" and lease["review"] is True, lease
    git_pull(env.hub_dir, wa.space_dir)
    wa.append("item.complete", {"id": "t", "result": "unreviewed", "evidence": []})
    item = hub_item(hub, "t")
    assert item.status == "submitted" and item.submission is not None and not pool_accepted(item)


def test_round3_pool_work_addressed_by_its_creator_still_needs_the_independent_review(env):
    """The legitimate path: the creator (or the approver) may address a pool
    task to a named agent, after a lease or before any; the review stays."""
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    boss = env.writer(TEST_APPROVER)
    pool_item(own, "leased")
    pool_item(own, "fresh")
    wb.request_lease("leased")
    wb.append("item.release", {"id": "leased"})
    git_pull(env.hub_dir, own.space_dir)                                # the creator has read the release
    own.append("item.update", {"id": "leased", "assignee": "wa"})       # the creator re-addresses it
    git_pull(env.hub_dir, boss.space_dir)
    boss.append("item.update", {"id": "fresh", "assignee": "wa"})       # the approver, before any lease
    for iid in ("leased", "fresh"):
        assert hub_item(hub, iid).payload.get("assignee") == "wa", hub_item(hub, iid).history
        assert refused(wb.request_lease, iid).code == "not_eligible"    # addressed: only wa may lease it
        lease = wa.request_lease(iid)["lease"]["payload"]
        assert lease["class"] == "pool" and lease["review"] is True, (iid, lease)
        git_pull(env.hub_dir, wa.space_dir)
        wa.append("item.complete", {"id": iid, "result": "done", "evidence": []})
        assert hub_item(hub, iid).status == "submitted", hub_item(hub, iid).history
        git_pull(env.hub_dir, wb.space_dir)
        wb.append("item.verify", {"id": iid, "check": DONE})            # another principal's check
        item = hub_item(hub, iid)
        assert item.status == "verified" and pool_accepted(item), item.history


def test_round3_pool_work_with_its_role_removed_is_still_taken_only_through_a_lease(env):
    """A creator that strips the role and addresses the task cannot open a
    plain-claim path around the review either."""
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "p1")
    own.append("item.update", {"id": "p1", "role": None, "assignee": "wa"})
    err = refused(submit_raw, hub, wa, "item.claim", {"id": "p1"})
    assert err.code == "not_authorized" and "lease" in err.message, err
    assert hub_item(hub, "p1").status == "created"


def test_round3_owner_set_cannot_give_an_agent_pool_work_outside_a_lease(env):
    """`owner.set` names an owner without a lease. On pool work the owner is
    the hub's lease holder: an agent (or the creator) cannot set one, and an
    owner the approver sets still cannot complete without a lease and review."""
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    boss = env.writer(TEST_APPROVER)
    pool_item(own, "p1")
    for w in (wa, own):
        err = refused(w.append, "owner.set", {"id": "p1", "owner": "wa"})
        assert err.code == "not_authorized", (w.actor, err)
    assert hub_item(hub, "p1").owner is None
    git_pull(env.hub_dir, boss.space_dir)
    boss.append("owner.set", {"id": "p1", "owner": "wa"})               # the admin override
    assert hub_item(hub, "p1").owner == "wa"
    err = refused(submit_raw, hub, wa, "item.complete", {"id": "p1", "result": "r", "evidence": []})
    assert err.code == "not_authorized", err
    assert hub_item(hub, "p1").status == "created"
    # It is still leased as pool work, and its completion is a submission.
    lease = wa.request_lease("p1")["lease"]["payload"]
    assert lease["class"] == "pool" and lease["review"] is True
    git_pull(env.hub_dir, wa.space_dir)
    wa.append("item.complete", {"id": "p1", "result": "r", "evidence": []})
    assert hub_item(hub, "p1").status == "submitted"
