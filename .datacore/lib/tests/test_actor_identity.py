"""One resolver, one order: env, identity file, registry, then hostname with a warning."""
import importlib.util, os, pathlib, sys

LIB = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ai", LIB / "actor_identity.py")
AI = importlib.util.module_from_spec(spec); spec.loader.exec_module(AI)


def _infra(tmp_path, hostname="bridge", actor="winston"):
    p = tmp_path / "infrastructure.yaml"
    p.write_text(f"servers:\n  winston:\n    access:\n      actor: {actor}\n      hostname: {hostname}\n")
    return p


def test_env_wins_then_identity_file_then_registry(tmp_path, monkeypatch):
    ident = tmp_path / "identity.env"; ident.write_text("export DATACORE_ACTOR='data'\n")
    infra = _infra(tmp_path, hostname=AI.short_hostname())
    monkeypatch.setenv("DATACORE_ACTOR", "Tris")
    assert AI.resolve(ident, infra) == ("tris", "env")
    monkeypatch.delenv("DATACORE_ACTOR")
    assert AI.resolve(ident, infra) == ("data", "identity.env")
    ident.unlink()
    assert AI.resolve(ident, infra) == ("winston", "registry")


def test_registry_matches_hostname_or_server_name_and_lowercases(tmp_path):
    infra = _infra(tmp_path, hostname="Bridge", actor="Winston")
    assert AI.registry_actor("bridge", infra) == "winston"
    assert AI.registry_actor("winston", infra) == "winston"
    assert AI.registry_actor("nowhere", infra) is None


def test_undeclared_machine_warns_once_and_strict_raises(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    monkeypatch.setattr(AI, "IDENTITY_FILE", tmp_path / "none.env")
    monkeypatch.setattr(AI, "INFRA", tmp_path / "none.yaml")
    monkeypatch.setattr(AI, "_warned", False)
    assert AI.this_actor() == AI.short_hostname()
    assert AI.this_actor() == AI.short_hostname()
    assert capsys.readouterr().err.count("no declaration") == 1
    import pytest
    with pytest.raises(AI.UndeclaredActor):
        AI.this_actor(strict=True)


def test_principal_binds_writer_logs_to_emails(tmp_path):
    p = tmp_path / "principals.yaml"
    p.write_text("principals:\n  miles:\n    emails: [miles@datacore.one]\n    writes_as: [miles, nightshift]\n")
    assert AI.principal_of("nightshift", p)[0] == "miles"
    assert AI.allowed_emails("NIGHTSHIFT", p) == {AI.email_hash("miles@datacore.one")}
    assert AI.allowed_emails("unknown", p) == set()


def test_explicit_registry_declares_every_expected_writer(tmp_path):
    registry = tmp_path / 'principals.yaml'
    registry.write_text('principals:\n'
                        '  human: {writes_as: [mac, data]}\n'
                        '  agent: {writes_as: [winston, miles, nightshift, tris]}\n'
                        '  migration: {writes_as: [genesis, bridge]}\n')
    ps = AI.principals(registry)
    bound = {w for p in ps.values() for w in (p.get("writes_as") or [])} | set(ps)
    for writer in ("mac", "winston", "miles", "nightshift", "tris", "data", "genesis", "bridge"):
        assert writer in bound, writer
        assert AI.principal_of(writer, registry)[0] in ps
    assert AI.principal_of('unregistered-writer', registry) == (None, {})


def test_bundled_registry_template_has_unambiguous_identity():
    registry = LIB.parent / 'registry/principals.yaml.example'
    ps = AI.principals(registry)
    assert set(ps) == {'owner', 'teammate', 'assistant'}
    for name, entry in ps.items():
        for writer in [name, *entry.get('writes_as', [])]:
            assert AI.principal_of(writer, registry)[0] == name


# --- Who counts as the addressee -----------------------------------------
# One function, because the dispatcher used to answer this with a string
# compare while the gate resolved principals -- and a principal here has more
# than one writer name.

def _roster(tmp_path):
    p = tmp_path / "principals.yaml"
    p.write_text("principals:\n"
                 "  miles: {writes_as: [miles, nightshift]}\n"
                 "  winston: {writes_as: [winston, bridge]}\n")
    return p


def test_an_executors_own_writer_name_is_not_someone_else(tmp_path):
    # nightshift's host writes as `miles`; its executor log is `nightshift`.
    # Both are the same principal, so work addressed to either is its own.
    r = _roster(tmp_path)
    assert AI.addressed_to("miles", "nightshift", r)
    assert AI.addressed_to("nightshift", "miles", r)
    assert AI.addressed_to("winston", "bridge", r)


def test_another_principals_work_is_still_declined(tmp_path):
    r = _roster(tmp_path)
    assert not AI.addressed_to("winston", "miles", r)
    assert not AI.addressed_to("miles", "bridge", r)


def test_unaddressed_work_is_open_to_whoever_gets_there_first(tmp_path):
    r = _roster(tmp_path)
    for nobody in (None, ""):
        assert AI.addressed_to("winston", nobody, r)


def test_two_unregistered_names_are_not_the_same_principal(tmp_path):
    # Both resolve to no principal. Sharing "None" must not make a stranger
    # the addressee of another stranger's work.
    r = _roster(tmp_path)
    assert not AI.addressed_to("someone", "somebody", r)
    assert AI.addressed_to("someone", "someone", r)


def test_a_writer_name_matches_whatever_its_case(tmp_path):
    # principal_of lowercases; a payload written by hand may not.
    r = _roster(tmp_path)
    assert AI.addressed_to("miles", "Nightshift", r)
