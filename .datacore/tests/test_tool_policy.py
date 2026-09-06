"""The in-flight tool-call policy (datacore#30): classification by
tool_effects.yaml, the decision per principal, the refusal on the ledger,
and the hook protocol. Fixture policy and ledger; never the real ones."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import tool_policy as tp  # noqa: E402

EFFECTS = tp.load_effects()   # the shipped vocabulary, config/tool_effects.yaml


@pytest.fixture
def policy_file(tmp_path):
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1, "approver": "human",
        "cosign_effects": ["email.send", "payment", "prod.deploy"],
        "known_effects": ["email.send", "payment", "prod.deploy"],
        "principals": {
            "gregor": {},
            "miles": {"never_effects": ["payment"], "may_delegate_to": ["tris", "data"]},
            "tris": {"never_effects": ["payment", "prod.deploy"]},
        },
    }))
    return p


# ── classification ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,inp,expected", [
    ("Bash", {"command": "curl -X POST https://api.stripe.com/v1/charges -d amount=100"}, {"payment"}),
    ("Bash", {"command": "python3 ~/Data/.datacore/lib/winston_send.py 'hello'"}, {"email.send"}),
    ("Bash", {"command": "gh release create v1.2.0 --notes x"}, {"prod.deploy"}),
    ("Bash", {"command": "sudo systemctl restart datacored"}, {"prod.deploy"}),
    ("Bash", {"command": "ls -la && git status"}, set()),
    ("Bash", {"command": "grep -rn 'stripe' docs/ | head"}, set()),
    ("WebFetch", {"url": "https://api.paypal.com/v2/checkout/orders"}, {"payment"}),
    ("mcp__gmail__send_email", {"to": "x@example.org", "body": "hi"}, {"email.send"}),
    ("mcp__gateio__place_order", {"pair": "BTC_USDT"}, {"payment"}),
    # Reading, editing or writing cannot act: no effect, whatever the text says.
    ("Read", {"file_path": "/x/deploy.sh"}, set()),
    ("Edit", {"file_path": "/x/pay.py", "new_string": "requests.post('https://api.stripe.com/v1/charges')"}, set()),
])
def test_classify_by_the_shipped_vocabulary(tool, inp, expected):
    assert tp.classify(tool, inp, EFFECTS) == expected


def test_call_text_prefers_command_fields_then_json():
    assert tp.call_text({"command": "ls", "description": "list"}) == "ls"
    assert json.loads(tp.call_text({"a": 1, "b": [2]})) == {"a": 1, "b": [2]}
    assert tp.call_text("raw") == "raw"
    assert tp.call_text(None) == ""


# ── decision ────────────────────────────────────────────────────────────────

def test_never_effect_is_refused_whatever_the_grants(policy_file):
    d = tp.decide("miles", "Bash", {"command": "curl https://api.stripe.com/v1/charges"},
                  granted=["payment"], effects=EFFECTS, policy_path=policy_file)
    assert d.blocked and d.kind == "never" and "may never cause payment" in d.reason


def test_cosign_effect_without_grant_is_paused(policy_file):
    d = tp.decide("miles", "Bash", {"command": "python3 winston_send.py 'x'"},
                  effects=EFFECTS, policy_path=policy_file)
    assert d.blocked and d.kind == "cosign"
    assert "email.send needs a co-signed grant" in d.reason and "proposal" in d.reason


def test_cosign_effect_with_grant_is_allowed(policy_file):
    d = tp.decide("miles", "Bash", {"command": "python3 winston_send.py 'x'"},
                  granted=["email.send"], effects=EFFECTS, policy_path=policy_file)
    assert d.allow and d.kind == "granted"


def test_plain_calls_are_allowed(policy_file):
    d = tp.decide("tris", "Bash", {"command": "pytest -q"}, effects=EFFECTS, policy_path=policy_file)
    assert d.allow and d.kind == "allow" and d.effects == set()


def test_tris_may_never_deploy(policy_file):
    d = tp.decide("tris", "Bash", {"command": "npm publish"}, effects=EFFECTS, policy_path=policy_file)
    assert d.blocked and d.kind == "never" and "prod.deploy" in d.reason


def test_unlisted_principal_gets_global_cosign_and_no_nevers(policy_file):
    never, cosign = tp.limits_for("someone-new", policy_file)
    assert never == set() and cosign == {"email.send", "payment", "prod.deploy"}


# ── the record ──────────────────────────────────────────────────────────────

def _space(tmp_path):
    (tmp_path / "space" / ".datacore" / "events").mkdir(parents=True)
    return tmp_path / "space"


def test_refusal_is_recorded_on_the_task_space_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    space = _space(tmp_path)
    d = tp.Decision(False, {"payment"}, "miles may never cause payment", "never")
    assert tp.record_refusal(d, principal="miles", tool_name="Bash", space_dir=space,
                             task_id="org-1", actor="nightshift", detail="curl api.stripe.com")
    from ledger.log import read_events
    ev = read_events(space)
    assert len(ev) == 1 and ev[0].type == "metric.attest" and ev[0].actor == "nightshift"
    p = ev[0].payload
    assert p["metric"] == "policy.refusal" and p["principal"] == "miles"
    assert p["task"] == "org-1" and p["effects"] == ["payment"] and p["kind"] == "never"


def test_record_is_best_effort_without_a_ledger(tmp_path, capsys):
    d = tp.Decision(False, {"payment"}, "x", "never")
    assert tp.record_refusal(d, principal="miles", tool_name="Bash", space_dir=tmp_path / "nowhere",
                             actor="nightshift") is False
    assert "not recorded" in capsys.readouterr().err


# ── the hook ────────────────────────────────────────────────────────────────

def test_hook_denies_never_effect_and_records(tmp_path, monkeypatch, policy_file):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("DATACORE_ACTOR", "nightshift")
    space = _space(tmp_path)
    env = {"DATACORE_POLICY_PRINCIPAL": "miles", "DATACORE_POLICY_SPACE": str(space),
           "DATACORE_POLICY_TASK": "org-42"}
    out = tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "curl https://api.stripe.com/v1/charges"}},
                           env=env, effects=EFFECTS, policy_path=policy_file)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "may never cause payment" in out["hookSpecificOutput"]["permissionDecisionReason"]
    from ledger.log import read_events
    ev = read_events(space)
    assert ev[-1].payload["task"] == "org-42" and ev[-1].payload["tool"] == "Bash"


def test_hook_allows_plain_calls_and_granted_effects(tmp_path, policy_file):
    env = {"DATACORE_POLICY_PRINCIPAL": "miles", "DATACORE_POLICY_GRANTED": "email.send"}
    assert tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}},
                            env=env, effects=EFFECTS, policy_path=policy_file) is None
    assert tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "python3 winston_send.py x"}},
                            env=env, effects=EFFECTS, policy_path=policy_file) is None


def test_absent_policy_file_means_the_shipped_defaults(tmp_path):
    # ledger.policy falls back to its built-in defaults (the three cosign
    # effects, no principals) when the file is absent — so a deploy without
    # a grant is still paused, not waved through.
    env = {"DATACORE_POLICY_PRINCIPAL": "miles"}
    out = tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "npm publish"}},
                           env=env, effects=EFFECTS, policy_path=tmp_path / "missing.yaml", record=False)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_fails_open_when_policy_is_unreadable(tmp_path, capsys):
    bad = tmp_path / "broken.yaml"
    bad.write_text("approver: [unclosed\ncosign_effects: {")
    env = {"DATACORE_POLICY_PRINCIPAL": "miles"}
    out = tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "npm publish"}},
                           env=env, effects=EFFECTS, policy_path=bad)
    assert out is None and "call allowed" in capsys.readouterr().err


def test_hook_main_protocol(monkeypatch, capsys, tmp_path, policy_file):
    monkeypatch.setattr(tp, "DEFAULT_POLICY_FILE", policy_file)
    monkeypatch.setenv("DATACORE_POLICY_PRINCIPAL", "tris")
    monkeypatch.setenv("DATACORE_POLICY_SPACE", str(tmp_path / "nowhere"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "twine upload dist/*"}})))
    assert tp.hook_main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert tp.hook_main() == 0


def test_settings_json_wires_the_guard_on_every_tool():
    s = json.loads(tp.settings_json(Path("/opt/x/guard.py")))
    hook = s["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "*"
    assert hook["hooks"][0]["command"] == "python3 /opt/x/guard.py"
    assert tp.GUARD.name == "tool_policy_guard.py" and tp.GUARD.exists()


def test_principal_for_maps_a_writer_to_its_principal(tmp_path, monkeypatch):
    import actor_identity as ai
    reg = tmp_path / "principals.yaml"
    reg.write_text(yaml.safe_dump({"principals": {
        "miles": {"kind": "agent", "writes_as": ["miles", "nightshift"]}}}))
    monkeypatch.setattr(ai, "PRINCIPALS", reg)
    assert tp.principal_for("nightshift") == "miles"
    assert tp.principal_for("miles") == "miles"
    assert tp.principal_for("stranger") == "stranger"
