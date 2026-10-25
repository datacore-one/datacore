"""Tests for bin/fleet-hire: hiring an agent from a runtime template.

A hire is proposed (the exact payload, checked against the templates and the
licence cap), approved by the owner in the ledger (`approval.grant` bound to
that payload), and only then carried out: the controller records the hire as a
co-signed `item.create` (effect `agent.hire`) and runs the playbook steps. No
grant, an edited proposal, or one agent over the cap: nothing happens.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

K3S = Path(__file__).resolve().parents[1]
LIB = K3S.parent
sys.path.insert(0, str(LIB))


def load_tool():
    loader = importlib.machinery.SourceFileLoader("fleet_hire", str(K3S / "bin" / "fleet-hire"))
    spec = importlib.util.spec_from_loader("fleet_hire", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A disposable space, principals and the approvals policy with the hire effects."""
    import actor_identity
    import ledger.keys
    import ledger.policy
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    # Real signatures with disposable keys: the grant must verify.
    monkeypatch.setattr(ledger.keys, "DEFAULT_KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(ledger.keys, "DEFAULT_REGISTRY_PATH", tmp_path / "keys/registry.yaml")
    reg = tmp_path / "principals.yaml"
    reg.write_text("principals:\n  human: {kind: human}\n  winston: {kind: agent}\n")
    pol = tmp_path / "approvals.yaml"
    pol.write_text("version: 1\napprover: human\ncosign_effects: [agent.hire, agent.retire]\n"
                   "principals:\n  winston: {}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", pol)
    space = tmp_path / "space"
    (space / ".datacore/events").mkdir(parents=True)
    return space


@pytest.fixture
def values(tmp_path):
    v = tmp_path / "values.yaml"
    v.write_text(yaml.safe_dump({
        "maxAgents": 3,
        "runtimes": {"hermes": {"image": "agent-hermes:x"}, "openclaw": {"image": "agent-openclaw:x"}},
        "agents": {"tris": {"runtime": "hermes"}, "data": {"runtime": "openclaw"}},
    }))
    return v


SPEC = {"name": "scout", "runtime": "openclaw", "role": "research scout",
        "storage": "10Gi", "crontab": "*/30 * * * * true\n"}


def approve(space, payload):
    from ledger.log import EventLog
    from ledger.policy import approval_payload_hash
    return EventLog(space, "human", sign=True).append(
        "approval.grant", {"item": payload["id"], "payload_hash": approval_payload_hash(payload)})


def test_a_proposal_is_checked_against_templates_and_cap(values):
    fh = load_tool()
    payload = fh.propose(SPEC, values)
    assert payload["id"] == "hire-scout" and payload["effects"] == ["agent.hire"]
    assert payload["hire"]["runtime"] == "openclaw"
    with pytest.raises(fh.HireRefused, match="runtime"):
        fh.propose({**SPEC, "runtime": "nonesuch"}, values)
    with pytest.raises(fh.HireRefused, match="already"):
        fh.propose({**SPEC, "name": "tris"}, values)


def test_one_agent_over_the_licence_cap_is_refused(values):
    fh = load_tool()
    d = yaml.safe_load(values.read_text())
    d["agents"]["winston"] = {"runtime": "hermes"}
    values.write_text(yaml.safe_dump(d))
    with pytest.raises(fh.HireRefused, match="cap"):
        fh.propose(SPEC, values)


def test_a_hire_without_the_owners_grant_is_refused(ledger, values):
    fh = load_tool()
    payload = fh.propose(SPEC, values)
    with pytest.raises(fh.HireRefused, match="grant"):
        fh.record(ledger, "winston", payload)


def test_a_granted_hire_is_recorded_once(ledger, values):
    from ledger.log import read_events
    fh = load_tool()
    payload = fh.propose(SPEC, values)
    approve(ledger, payload)
    event = fh.record(ledger, "winston", payload)
    assert event.type == "item.create" and event.payload["effects"] == ["agent.hire"]
    assert any(e.type == "item.create" and e.payload.get("id") == "hire-scout" for e in read_events(ledger))
    with pytest.raises(Exception):
        fh.record(ledger, "winston", payload)   # a grant authorises one creation


def test_an_edited_proposal_needs_a_new_grant(ledger, values):
    fh = load_tool()
    payload = fh.propose(SPEC, values)
    approve(ledger, payload)
    edited = json.loads(json.dumps(payload))
    edited["hire"]["storage"] = "500Gi"
    with pytest.raises(fh.HireRefused, match="grant"):
        fh.record(ledger, "winston", edited)


def test_the_agent_entry_is_rendered_from_the_proposal(values):
    fh = load_tool()
    entry = fh.agent_entry(fh.propose(SPEC, values)["hire"])
    assert entry == {"runtime": "openclaw", "running": False, "signingKeySecret": "scout-signing-key",
                     "signingKeyName": "scout.key", "storage": "10Gi", "crontab": "*/30 * * * * true\n"}


def test_the_chart_refuses_more_agents_than_the_cap():
    chart = K3S / "chart" / "fleet"
    r = subprocess.run(["helm", "template", "fleet", str(chart), "-f", str(K3S / "tests/fixtures/values-runtimes.yaml"),
                        "--set", "maxAgents=1"], capture_output=True, text=True)
    assert r.returncode != 0 and "maxAgents" in r.stderr


def test_running_the_controller_is_a_cosign_effect_for_an_agent():
    import tool_policy
    effects = tool_policy.load_effects(LIB.parent / "config" / "tool_effects.yaml")
    for cmd in ("fleet-hire hire --proposal p.json", "python3 bin/fleet-hire retire --proposal p.json",
                "helm upgrade fleet chart/fleet -n firm -f values.yaml"):
        assert "agent.hire" in tool_policy.classify("Bash", {"command": cmd}, effects), cmd
    assert "agent.hire" not in tool_policy.classify("Bash", {"command": "fleet-hire propose spec.yaml"}, effects)
    policy = yaml.safe_load((LIB.parent / "config" / "approvals_policy.yaml").read_text())
    assert {"agent.hire", "agent.retire"} <= set(policy["cosign_effects"]) <= set(policy["known_effects"])


def test_a_recorded_hire_resumes_instead_of_recording_twice(ledger, values):
    # Found in the rehearsal: the hire was recorded, then a later step failed.
    # The grant authorises one creation; carrying on must not need a second.
    fh = load_tool()
    payload = fh.propose(SPEC, values)
    approve(ledger, payload)
    first = fh.record(ledger, "winston", payload)
    again = fh.recorded(ledger, payload)
    assert again is not None and again.hash == first.hash
    edited = json.loads(json.dumps(payload))
    edited["hire"]["storage"] = "500Gi"
    assert fh.recorded(ledger, edited) is None, "only the exact approved content resumes"
