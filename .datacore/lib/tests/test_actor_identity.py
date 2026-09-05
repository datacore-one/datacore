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
    assert AI.allowed_emails("NIGHTSHIFT", p) == {"miles@datacore.one"}
    assert AI.allowed_emails("unknown", p) == set()


def test_the_real_registry_declares_every_known_writer():
    ps = AI.principals()
    bound = {w for p in ps.values() for w in (p.get("writes_as") or [])} | set(ps)
    for writer in ("mac", "winston", "miles", "nightshift", "tris", "data", "genesis", "bridge"):
        assert writer in bound, writer
