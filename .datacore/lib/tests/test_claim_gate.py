"""Who may claim, who may create, how deep, how many — decided before the append."""
import datetime as dt, importlib.util, json, pathlib, sys
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import claim_gate as G  # noqa: E402
from ledger.policy import Policy, load_policy, guarded_append, PolicyError  # noqa: E402
from ledger.log import EventLog  # noqa: E402
import pytest  # noqa: E402


def _policy(**principals):
    return Policy(approver="human", cosign_effects=frozenset({"email.send"}), principals=principals or None)


def _reg(monkeypatch, tmp_path):
    p = tmp_path / "principals.yaml"
    p.write_text("principals:\n  gregor: {kind: human, writes_as: [mac]}\n  miles: {kind: agent, writes_as: [miles, nightshift]}\n  data: {kind: agent, writes_as: [data]}\n")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    return p


def test_unregistered_writer_cannot_claim_or_create(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    assert G.check_claim("ghost", {})[0] is False
    assert "unregistered" in G.check_create("ghost", {})[1]
    assert G.check_claim("nightshift", {})[0] is True  # bound to miles


def test_a_never_effect_is_refused_at_claim(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    pol = _policy(data={"never_effects": ["payment"]})
    ok, why = G.check_claim("data", {"effects": ["payment"]}, policy=pol)
    assert not ok and "may never perform payment" in why
    assert G.check_claim("data", {"effects": ["email.send"]}, policy=pol)[0]


def test_hops_and_delegation_are_bounded_for_agents_not_humans(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    pol = _policy(miles={"may_delegate_to": ["data"]})
    assert G.check_create("miles", {"hops": 3}, policy=pol)[0]
    ok, why = G.check_create("miles", {"hops": 4}, policy=pol)
    assert not ok and "4 hops" in why
    assert G.check_create("mac", {"hops": 40}, policy=pol)[0], "the human is unbounded"
    ok, why = G.check_create("miles", {"assignee": "tris", "requested_by": "miles"}, policy=pol)
    assert not ok and "may not delegate to tris" in why
    assert G.check_create("miles", {"assignee": "data", "requested_by": "miles"}, policy=pol)[0]


def test_daily_creation_allowance_counts_this_writers_own_log(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    space = tmp_path / "5-plur"; (space / ".datacore" / "events").mkdir(parents=True)
    log = EventLog(space, "data")
    for i in range(3):
        log.append("item.create", {"id": f"i{i}", "title": "x"})
    pol = _policy(data={"max_creates_per_day": 3})
    ok, why = G.check_create("data", {"id": "i9"}, policy=pol, space_dir=space)
    assert not ok and "allowance is 3" in why
    assert G.creates_today(space, "data", today=dt.date(2000, 1, 1)) == 0


def test_guarded_append_refuses_a_create_past_the_gate(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    space = tmp_path / "5-plur"; (space / ".datacore" / "events").mkdir(parents=True)
    log = EventLog(space, "data")
    pol = _policy(data={"max_hops": 1})
    with pytest.raises(PolicyError, match="hops"):
        guarded_append(log, "item.create", {"id": "deep", "title": "x", "hops": 2}, policy=pol, space_dir=space)
    guarded_append(log, "item.create", {"id": "ok", "title": "x", "hops": 1}, policy=pol, space_dir=space)


def test_policy_file_loads_principals_and_rejects_bad_shapes(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("version: 1\napprover: human\ncosign_effects: [email.send]\nprincipals:\n  data: {never_effects: [payment], max_hops: 2}\n")
    pol = load_policy(f)
    assert pol.principals == {"data": {"never_effects": ["payment"], "max_hops": 2}}
    f.write_text("version: 1\napprover: human\ncosign_effects: [email.send]\nprincipals:\n  data: {max_hops: -1, bogus: 1}\n")
    with pytest.raises(PolicyError, match="max_hops|unknown"):
        load_policy(f)
    assert load_policy(LIB.parent / "config" / "approvals_policy.yaml").principals is not None
