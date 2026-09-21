"""A visitor carries only what is about itself.

The roster has said "A WORKSTATION IS NOT A DEGRADED SERVER" since before this
test existed, and nothing enforced it. The laptop accumulated twenty job
contracts -- most of them network duties with clock deadlines, seven added in
three days by sessions that were fixing laptop alerts -- and spent a month
alerting about its own lid. Each alert got its own patch: awake-time accounting,
a dark-wake hold, launchd for cron, a grace period measured on the right clock.
One missing rule, applied piecemeal.

This is the rule. A node whose presence is not promised (jobs.awake.node_class
== "visitor") may carry a job only if the job says why it belongs there:

    scope: local    it is about this machine: its working copy, its own feed
    scope: pinned   a network duty stuck here by a stated constraint, with the
                    condition that frees it (`unpin_when`). Pinned is debt, and
                    the point of naming it is that it can be counted.

Anything else belongs on a resident, where a deadline is an honest thing to
have. Adding a teammate's laptop is then one roster entry and no new alert
surface, which is the property that has to hold as the number of humans grows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from jobs import awake  # noqa: E402

MANIFEST = LIB / "jobs" / "manifest.yaml"


def _roster(tmp_path, **servers) -> Path:
    p = tmp_path / "infrastructure.yaml"
    p.write_text(yaml.safe_dump({"servers": servers}))
    return p


def _visitor_jobs() -> list[dict]:
    jobs = yaml.safe_load(MANIFEST.read_text())["jobs"]
    return [j for j in jobs if awake.node_class(j.get("machine", "")) == "visitor"]


def test_a_workstation_is_a_visitor_and_a_server_is_a_resident(tmp_path):
    roster = _roster(tmp_path, lap={"kind": "workstation"}, srv={"kind": "server"},
                     pinned_up={"kind": "workstation", "always_on": True})
    assert awake.node_class("lap", roster) == "visitor"
    assert awake.node_class("srv", roster) == "resident"
    # An explicit promise outranks the kind: a desktop that never sleeps is a
    # resident whatever it is called.
    assert awake.node_class("pinned_up", roster) == "resident"


def test_an_undeclared_machine_is_a_resident(tmp_path):
    """A host becomes a visitor only by being declared one, so adding this type
    changed no existing contract's behaviour."""
    assert awake.node_class("never-heard-of-it", _roster(tmp_path)) == "resident"


def test_this_installation_actually_has_a_visitor():
    """Guards the guard: if the roster stops classing the laptop as a visitor,
    every test below passes vacuously over an empty list."""
    assert _visitor_jobs(), "no job runs on a visitor -- is the roster's kind: workstation gone?"


def test_every_job_on_a_visitor_says_why_it_is_there():
    bad = [j["name"] for j in _visitor_jobs() if j.get("scope") not in ("local", "pinned")]
    assert not bad, (
        "these jobs run on a machine whose presence is not promised and do not say why:\n  "
        + "\n  ".join(bad)
        + "\nA network duty with a deadline belongs on a resident (nightshift, box). If the "
          "job is genuinely about this machine, add `scope: local` and `why_local:`. If a "
          "constraint forces it here, add `scope: pinned`, `why_pinned:` and `unpin_when:`."
    )


def test_a_local_job_states_its_reason():
    for j in _visitor_jobs():
        if j.get("scope") == "local":
            assert str(j.get("why_local") or "").strip(), f"{j['name']}: scope: local with no why_local"


def test_a_pinned_job_names_the_condition_that_frees_it():
    """Pinned is debt. Debt with no repayment condition is just a permanent
    exception wearing a label."""
    for j in _visitor_jobs():
        if j.get("scope") == "pinned":
            assert str(j.get("why_pinned") or "").strip(), f"{j['name']}: pinned with no reason"
            assert str(j.get("unpin_when") or "").strip(), f"{j['name']}: pinned with no way out"


def test_pinned_debt_does_not_grow_unnoticed():
    """Three on 2026-09-21: config-drift, registry-gc, suite-audit. Raising this
    number is a decision someone should be seen making."""
    pinned = sorted(j["name"] for j in _visitor_jobs() if j.get("scope") == "pinned")
    assert len(pinned) <= 3, f"pinned duties on a visitor grew to {len(pinned)}: {pinned}"


def test_the_rule_refuses_a_network_duty_on_a_visitor(tmp_path, monkeypatch):
    """Prove the guard bites, rather than trusting that it would."""
    doc = {"jobs": [{"name": "lap-fleet-poll", "machine": "lap", "schedule": "0 * * * *",
                     "cmd": "true", "artifacts": [{"path": "~/x", "check": "exists"}]}]}
    fake = tmp_path / "manifest.yaml"
    fake.write_text(yaml.safe_dump(doc))
    roster = _roster(tmp_path, lap={"kind": "workstation"})
    monkeypatch.setattr(sys.modules[__name__], "MANIFEST", fake)
    monkeypatch.setattr(awake, "always_on", lambda m, r=None: m != "lap")
    with pytest.raises(AssertionError, match="lap-fleet-poll"):
        test_every_job_on_a_visitor_says_why_it_is_there()
