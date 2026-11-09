"""A repair is created once per job per day, not once per verification run.

job_verify's own comment promised this ("churn is bounded by the item id, which
is one per job per day: repeated failures of the same job reuse it"), and
`delegate()`'s docstring lists an `exists` state -- but nothing checked. Every
verification run whose artifact had changed appended ANOTHER `item.create` for
the same `autofix-<job>-<date>` id. The fold ignores the repeat; the creation
allowance does not, because it counts `item.create` events.

Measured on winston, 2026-09-30: 50 creates in 2-datacore, the whole daily
allowance, of which 30 were repeats of four ids (box-ledger-ingest 12x,
box-phase1-cycle 12x, box-briefing 4x, box-cadence-liveness 2x). From then on
every NEW repair, every job-verify task and every audit finding winston tried to
file that day was refused: "winston has created 50 item(s) today; the
allowance is 50". Same shape on 09-27, 09-28 and 09-29.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _job(name="nightshift-x", machine="nightshift", artifact=None):
    arts = [types.SimpleNamespace(path=str(artifact), max_age_hours=None)] if artifact else []
    return types.SimpleNamespace(name=name, machine=machine, delegate=True, cmd="x", schedule="x",
                                 artifacts=arts)


def _setup(monkeypatch, tmp_path):
    import autofix
    import ledger.policy
    import actor_identity
    appended = []
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: appended.append((kind, payload)))
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    roster = tmp_path / "infrastructure.yaml"
    roster.write_text("servers:\n"
                      "  nightshift: {kind: server, ledger_actors: [nightshift, miles], access: {actor: miles}}\n")
    space = tmp_path / "2-datacore"
    (space / ".datacore" / "events").mkdir(parents=True)
    return autofix, space, roster, appended


def test_a_repair_already_in_the_ledger_today_is_not_created_again(tmp_path, monkeypatch):
    autofix, space, roster, appended = _setup(monkeypatch, tmp_path)
    import time
    from ledger.log import EventLog
    iid = f"autofix-nightshift-x-{time.strftime('%Y%m%d', time.gmtime())}"
    EventLog(space, "winston").append("item.create", {"id": iid, "title": "Repair nightshift-x",
                                                      "assignee": "miles", "autofix": True})

    state, why = autofix.delegate(_job(), ["f"], {"first_failed": "2026-09-30"}, root=tmp_path,
                                  assignee="miles", roster=roster)

    assert appended == [], "a second item.create for the same id spends the creation allowance"
    assert state == "exists" and iid in why


def test_the_first_failure_of_the_day_is_still_delegated(tmp_path, monkeypatch):
    autofix, space, roster, appended = _setup(monkeypatch, tmp_path)

    state, why = autofix.delegate(_job(), ["f"], {"first_failed": "2026-09-30"}, root=tmp_path,
                                  assignee="miles", roster=roster)

    assert state == "delegated", why
    assert [k for k, _ in appended] == ["item.create"]


def test_an_existing_repair_is_not_escalated_to_the_operator(tmp_path, monkeypatch):
    """`exists` means the repair is already in hand: the operator is not paged
    for it, exactly as when it was delegated a run earlier."""
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    import job_verify as jv
    monkeypatch.setattr(jv, "_NO_EMIT", False)
    monkeypatch.setattr(jv, "_delegate_repair", lambda job, failures, rec: ("exists", "autofix-x already filed"))
    monkeypatch.setattr(jv, "_file_task", lambda job, rec, failures: None)
    sent = []
    monkeypatch.setattr(jv, "_send_telegram", lambda message: sent.append(message) or True)
    out = tmp_path / "out.log"
    out.write_text("bad\n")

    jv._dispatch_alert("telegram", "nightshift-x", ["bad"], job=_job(artifact=out))

    assert sent == [], "an already-filed repair paged the operator"
