"""Stages 2, 4, 5, 7, 8 root-side: budget at claim, grant co-sign, arbitration override, absence, signing, rows, outcomes."""
import datetime as dt, importlib.util, json, pathlib, sys, time
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import pytest  # noqa: E402
import claim_gate as G  # noqa: E402
from ledger.log import EventLog  # noqa: E402
from ledger.policy import Policy, guarded_append, PolicyError, load_policy  # noqa: E402


def _reg(monkeypatch, tmp_path, budget=None):
    p = tmp_path / "principals.yaml"
    p.write_text(f"principals:\n  gregor: {{kind: human, writes_as: [mac]}}\n  winston: {{kind: agent, writes_as: [winston]}}\n  miles: {{kind: agent, writes_as: [miles, nightshift]{', budget_monthly_usd: ' + str(budget) if budget is not None else ''}}}\n  data: {{kind: agent, writes_as: [data]}}\n")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    return p


def _space(tmp_path, name="5-plur"):
    sd = tmp_path / name; (sd / ".datacore" / "events").mkdir(parents=True); return sd


def test_budget_binds_at_claim_once_declared(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path, budget=10)
    sd = _space(tmp_path)
    log = EventLog(sd, "miles")
    log.append("spend.record", {"cents": 900, "ref": "llm"})
    assert G.check_claim("miles", {}, space_dir=sd)[0]
    log.append("spend.record", {"cents": 200, "ref": "llm"})
    ok, why = G.check_claim("miles", {}, space_dir=sd)
    assert not ok and "spent 11.00 of a 10" in why
    _reg(monkeypatch, tmp_path)  # undeclared: no ceiling, said so
    assert G.check_budget("miles", sd) == (True, "miles: no budget declared")


def test_only_the_approver_may_grant(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    sd = _space(tmp_path)
    pol = Policy(approver="mac", cosign_effects=frozenset({"email.send"}), principals={})
    with pytest.raises(PolicyError, match="only the approver"):
        guarded_append(EventLog(sd, "data"), "item.grant", {"id": "g1", "effects": ["email.send"]}, policy=pol, space_dir=sd)
    guarded_append(EventLog(sd, "mac"), "item.grant", {"id": "g1", "effects": ["email.send"]}, policy=pol, space_dir=sd)


def test_arbitration_order_decides_who_may_close_anothers_item(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    sd = _space(tmp_path)
    pol = Policy(approver="mac", cosign_effects=frozenset(), principals={}, arbitration=("gregor", "winston"))
    log = EventLog(sd, "miles")
    log.append("item.create", {"id": "t1", "title": "x"}); log.append("item.claim", {"id": "t1", "executor": "nightshift"})
    with pytest.raises(PolicyError, match="may not override"):
        guarded_append(EventLog(sd, "data"), "item.dismiss", {"id": "t1", "kind": "dropped", "reason": "no"}, policy=pol, space_dir=sd)
    guarded_append(EventLog(sd, "miles"), "item.release", {"id": "t1", "reason": "own"}, policy=pol, space_dir=sd)
    log.append("item.claim", {"id": "t1", "executor": "nightshift"})
    guarded_append(EventLog(sd, "winston"), "item.dismiss", {"id": "t1", "kind": "dropped", "reason": "arbitrated"}, policy=pol, space_dir=sd)


def test_policy_loads_arbitration():
    pol = load_policy(LIB.parent / "config" / "approvals_policy.yaml")
    assert pol.arbitration and pol.arbitration[0] == "gregor"


def test_absence_is_read_from_the_verifiers_attestations(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    root = tmp_path; sd = _space(root, "2-datacore")
    assert G.absent("data", root=root)[0] is True
    log = EventLog(sd, "data")
    log.append("metric.attest", {"metric": "job.verify", "job": "plur-claw-ledger-dispatch", "ok": True, "failures": []})
    is_absent, note = G.absent("data", root=root)
    assert not is_absent and "verified 0h ago" in note
    is_absent, _ = G.absent("data", root=root, now=time.time() + 30 * 3600)
    assert is_absent
    payload = {"id": "n1", "title": "for winston", "assignee": "winston", "requested_by": "data"}
    ok, _ = G.check_create("data", payload, policy=Policy(approver="mac", cosign_effects=frozenset(), principals={}), space_dir=sd)
    assert ok and "never heard from" in payload["assignee_absent"]


def test_signed_events_verify_with_the_registered_key(tmp_path, monkeypatch):
    from ledger.keys import verify as key_verify
    from ledger.events import canonical_bytes
    sd = _space(tmp_path); keys = tmp_path / "keys"; reg = tmp_path / "keys-registry.yaml"
    log = EventLog(sd, "miles", keys_dir=keys, registry_path=reg, sign=True)
    ev = log.append("item.create", {"id": "s1", "title": "signed"})
    line = json.loads((sd / ".datacore" / "events" / "miles.jsonl").read_text().splitlines()[-1])
    assert line.get("sig")
    body = {k: v for k, v in line.items() if k not in ("sig", "hash")}
    assert key_verify("miles", canonical_bytes(body), line["sig"], registry_path=reg)
    body["payload"] = {"id": "s1", "title": "tampered"}
    assert not key_verify("miles", canonical_bytes(body), line["sig"], registry_path=reg)


def test_principal_rows_and_outcomes(tmp_path, monkeypatch):
    _reg(monkeypatch, tmp_path)
    root = tmp_path; sd = _space(root, "2-datacore")
    spec = importlib.util.spec_from_file_location("rs", LIB / "reliability_scoreboard.py"); R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
    EventLog(sd, "nightshift").append("metric.attest", {"metric": "job.verify", "job": "nightshift-overnight", "ok": True, "failures": []})
    EventLog(sd, "miles").append("metric.attest", {"metric": "job.verify", "job": "nightshift-miles-bot", "ok": False, "failures": ["stale"]})
    (root / ".datacore" / "registry").mkdir(parents=True); (root / ".datacore" / "registry" / "principals.yaml").write_text((tmp_path / "principals.yaml").read_text())
    rows = {r["principal"]: r for r in R.principal_rows(root)}
    assert rows["miles"]["ok"] is False and "1/2 contracts" in rows["miles"]["note"] and "nightshift-miles-bot" in rows["miles"]["note"]
    assert rows["data"]["ok"] is None
    spec2 = importlib.util.spec_from_file_location("ao", LIB / "agent_outcomes.py"); A = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(A)
    log = EventLog(sd, "nightshift")
    for i, sc in enumerate((0.8, 0.6)):
        log.append("item.create", {"id": f"o{i}", "title": "x"}); log.append("item.claim", {"id": f"o{i}", "executor": "nightshift"})
        log.append("item.complete", {"id": f"o{i}", "score": sc, "agent": "research", "agent_version": "1.0.0", "model": "m1"})
    out = A.outcomes(root, days=1)
    row = next(r for r in out if r["agent"] == "research")
    assert row["completions"] == 2 and row["mean_score"] == 0.7 and row["version"] == "1.0.0"


def test_signing_switch_is_read_from_the_identity_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    ident = tmp_path / "identity.env"; ident.write_text("DATACORE_ACTOR=miles\nDATACORE_LEDGER_SIGN=1\n")
    monkeypatch.setenv("DATACORE_IDENTITY_FILE", str(ident))
    sd = _space(tmp_path)
    log = EventLog(sd, "miles", keys_dir=tmp_path / "k", registry_path=tmp_path / "r.yaml")
    assert log.sign is True
    log.append("item.create", {"id": "s2", "title": "signed by declaration"})
    assert json.loads((sd / ".datacore" / "events" / "miles.jsonl").read_text().splitlines()[-1])["sig"]
    ident.write_text("DATACORE_ACTOR=miles\n")
    assert EventLog(sd, "miles", keys_dir=tmp_path / "k", registry_path=tmp_path / "r.yaml").sign is False


def test_distributed_verify_key_verifies_a_signed_chain(tmp_path, monkeypatch):
    """A host that never saw the writer's key verifies its chain through
    principals.yaml's verify_keys; a writer with no key anywhere is
    unverifiable (not an error) unless strict."""
    from ledger import keys as K
    from ledger.verify import verify_chain
    sd = _space(tmp_path); keys_dir = tmp_path / "k"; reg = tmp_path / "r.yaml"
    log = EventLog(sd, "miles", keys_dir=keys_dir, registry_path=reg, sign=True)
    log.append("item.create", {"id": "s1", "title": "signed"})
    hexkey = __import__("yaml").safe_load(reg.read_text())["actors"]["miles"]
    # the verifying host: an EMPTY local registry, the key only in principals.yaml
    pr = tmp_path / "principals.yaml"; pr.write_text(f"principals:\n  miles: {{kind: agent, writes_as: [miles]}}\nverify_keys:\n  miles: {hexkey}\n")
    monkeypatch.setattr(K, "DATACORE_ROOT", tmp_path)
    (tmp_path / ".datacore" / "registry").mkdir(parents=True); (tmp_path / ".datacore" / "registry" / "principals.yaml").write_text(pr.read_text())
    empty = tmp_path / "empty.yaml"
    assert verify_chain(sd / ".datacore" / "events" / "miles.jsonl", registry_path=empty) == []
    # tamper: the signature no longer matches -> a real error, because the key is known
    f = sd / ".datacore" / "events" / "miles.jsonl"; line = json.loads(f.read_text().splitlines()[-1]); line["sig"] = "ab" * 64
    f.write_text(json.dumps(line, separators=(",", ":"), sort_keys=True) + "\n")
    errs = verify_chain(f, registry_path=empty)
    assert errs and "signature" in errs[0]
    # unknown writer: unverifiable, not an error; strict refuses
    (tmp_path / ".datacore" / "registry" / "principals.yaml").write_text("principals: {}\nverify_keys: {}\n")
    sd2 = tmp_path / "5-x"; (sd2 / ".datacore" / "events").mkdir(parents=True)
    EventLog(sd2, "ghost", keys_dir=keys_dir, registry_path=tmp_path / "r2.yaml", sign=True).append("item.create", {"id": "g", "title": "x"})
    assert verify_chain(sd2 / ".datacore" / "events" / "ghost.jsonl", registry_path=empty) == []
    assert verify_chain(sd2 / ".datacore" / "events" / "ghost.jsonl", registry_path=empty, strict=True)
