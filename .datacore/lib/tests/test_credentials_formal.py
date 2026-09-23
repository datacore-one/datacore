"""Credential cluster: counterexamples found by DatacoreSpec/Credentials.lean.

Each test is a replayed counterexample from the Lean model, pinned after the
fix. Everything runs on a tmp index and store with FAKE values; the network
is replaced by a fake urlopen and attestation by a no-op. See
.datacore/specs/datacore-lean/findings/credentials.md.
"""

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
import config_plane  # noqa: E402
import credential_access as ca  # noqa: E402
import creds  # noqa: E402
import env_utils  # noqa: E402
import model_routing as mr  # noqa: E402


class _Resp:
    status = 200

    def __init__(self, body: bytes):
        self._b = body

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """Tmp HOME, tmp index + env, fake network (2xx with a body that proves nothing)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DATACORE_INSTANCE", "test-host")
    secrets = tmp_path / ".datacore" / "secrets"
    env = tmp_path / ".datacore" / "env"
    secrets.mkdir(parents=True)
    env.mkdir(parents=True)
    index = secrets / "credential-index.yaml"
    index.write_text(yaml.dump({"credentials": [
        {"id": "forge-gitea", "name": "g", "provider": "gitea",
         "var_name": "GITEA_TOKEN", "api_base": "https://git.example.invalid"},
        {"id": "multi", "name": "m", "var_name": "OPENAI_API_KEY",
         "vars": ["OPENAI_API_KEY", "GH_TOKEN"]},
        {"id": "gem", "name": "x", "var_name": "GEMINI_API_KEY"},
        {"id": "off", "name": "o", "var_name": "DEVTO_API_KEY", "disabled": True,
         "disabled_reason": "switched off", "api_base": "https://h.example.invalid"},
        {"id": "tilde", "name": "t", "var_name": "TILDE_KEY",
         "storage": "~/fake-store.env"},
    ]}))
    (env / ".env").write_text(
        "GITEA_TOKEN=fake-gitea\nOPENAI_API_KEY=fake-openai\nGH_TOKEN=fake-gh\n"
        "GEMINI_API_KEY=fake-gem\nDEVTO_API_KEY=fake-off\n")
    (tmp_path / "fake-store.env").write_text("TILDE_KEY=fake-tilde\n")
    monkeypatch.setattr(ca, "INDEX", index)
    monkeypatch.setattr(ca, "ENV", env)
    monkeypatch.setattr(ca, "SECRETS", secrets)
    # doctor scans KNOWN_STORES (under DATA) for duplicate keys: keep it in tmp
    monkeypatch.setattr(ca, "DATA", tmp_path)
    monkeypatch.setattr(ca, "attest", lambda *a, **k: None)
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        return _Resp(b"{}")
    monkeypatch.setattr(ca, "_secret_urlopen", fake_urlopen)
    mgr = creds.CredentialManager(data_dir=str(tmp_path))
    mgr.calls = calls
    return mgr


def _run(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue(), err.getvalue()


# --- (1) serve decision --------------------------------------------------------

def test_get_refuses_what_the_full_entry_verifier_proves_dead(broker):
    """Lean: get_old_serves_dead. get skipped the api_base/provider verifier
    (n-a, served) while the gitea verifier says FAIL for the same value."""
    rc, out, err = _run(broker.cmd_get, "forge-gitea")
    assert rc == 1 and out == ""
    assert "DEAD" in err
    assert broker.calls == ["https://git.example.invalid/api/v1/user"]


def test_doctor_uses_provider_specific_verifier(broker):
    """Lean: doctor_old_passes_any_2xx. doctor dropped `provider`, fell through
    to the generic probe of the api_base root and reported any 2xx as ok."""
    rc, out, _ = _run(broker.cmd_doctor, "forge-gitea")
    assert "FAIL" in out and rc == 1
    assert broker.calls == ["https://git.example.invalid/api/v1/user"]


def test_get_and_doctor_agree_on_a_disabled_entry(broker):
    """Lean: get_agrees_with_doctor. `disabled` was applied by doctor only."""
    rc, out, err = _run(broker.cmd_get, "off")
    assert rc == 0 and out.strip() == "fake-off"
    assert "disabled by decision" in err
    assert broker.calls == []
    _, dout, _ = _run(broker.cmd_doctor, "off")
    assert "n-a" in dout and "disabled by decision" in dout


def test_get_verifies_the_variable_it_serves(broker, monkeypatch):
    """Lean: get_old_verifies_wrong_var. `creds get GH_TOKEN` served GH_TOKEN
    but probed OPENAI_API_KEY's verifier -- sending a GitHub token to OpenAI."""
    seen = []
    monkeypatch.setattr(ca, "verify_value",
                        lambda var, v, **k: (seen.append(var), ("ok", "stub"))[1])
    rc, out, _ = _run(broker.cmd_get, "GH_TOKEN")
    assert rc == 0 and out.strip() == "fake-gh"
    assert seen == ["GH_TOKEN"]


def test_na_is_served_with_notice_by_default_and_refused_under_strict(broker):
    """Lean: serve_ok_or_warned, strict_serves_only_ok."""
    rc, out, err = _run(broker.cmd_get, "gem")
    assert rc == 0 and out.strip() == "fake-gem"
    assert creds.NA_NOTICE_PREFIX in err  # decision C1: fixed prefix
    rc, out, err = _run(broker.cmd_get, "gem", strict=True)
    assert rc == 3 and out == ""
    assert "NOT served" in err


def test_lock_is_keyed_on_the_index_id(broker):
    """Lean: lock_key_canonical. Two names for one credential took two locks."""
    _run(broker.cmd_get, "gem")
    _run(broker.cmd_get, "GEMINI_API_KEY")
    locks = sorted(p.name for p in (Path(os.environ["HOME"]) / ".datacore" / "locks").iterdir())
    assert locks == ["cred-gem.lock"]


def test_doctor_without_index_reports_instead_of_crashing(tmp_path):
    mgr = creds.CredentialManager(data_dir=str(tmp_path / "nowhere"))
    rc, out, _ = _run(mgr.cmd_doctor)
    assert rc == 2 and "no credential index" in out


# --- (2) precedence and parsing --------------------------------------------------

def test_declared_path_store_is_home_expanded(broker):
    assert ca.get_value("tilde") == "fake-tilde"


@pytest.mark.parametrize("override", [False, True])
def test_load_env_files_agrees_with_resolve(broker, monkeypatch, override):
    """Lean: load_agrees_with_resolve. Fleet beat host for override=False."""
    env = ca.ENV
    (env / ".env").write_text("PREC_KEY=fleet\nONLY_FLEET=f\n")
    (env / "local.env").write_text("PREC_KEY=host\n")
    ca.INDEX.write_text(yaml.dump({"credentials": [
        {"id": "prec", "var_name": "PREC_KEY"}, {"id": "of", "var_name": "ONLY_FLEET"}]}))
    monkeypatch.setenv("DATACORE_ROOT", str(env.parent.parent))
    monkeypatch.delenv("PREC_KEY", raising=False)
    monkeypatch.delenv("ONLY_FLEET", raising=False)
    loaded = env_utils.load_env_files(override=override)
    try:
        assert os.environ["PREC_KEY"] == ca.get_value("prec") == "host"
        assert os.environ["ONLY_FLEET"] == ca.get_value("of") == "f"
        assert loaded["PREC_KEY"] == "host"
    finally:
        os.environ.pop("PREC_KEY", None)
        os.environ.pop("ONLY_FLEET", None)


def test_config_error_never_echoes_the_line(tmp_path):
    """Lean: config_error_redacted."""
    p = tmp_path / "c.env"
    p.write_text("FAKE-SECRET-sk_fake_000\nbad-key-sk_fake_111=v\n")
    with pytest.raises(config_plane.ConfigError) as ei:
        config_plane.load(p)
    msg = str(ei.value)
    assert "sk_fake_000" not in msg and "sk_fake_111" not in msg
    assert "line 1: no '=' found" in msg and "line 2: invalid key" in msg


# --- (3) model_routing privacy floor --------------------------------------------

def test_venture_floor_survives_escalation(monkeypatch):
    """Lean: pick_old_breaks_floor. dmcc contract-review -> claude-opus.

    Since decision C3 (2026-09-23) dmcc lists contract-review as an explicit
    floor exception, so the property is pinned on a venture shaped like the
    old dmcc, without exceptions (see test_decisions_creds.py for C3)."""
    cfg = dict(mr._load_config())
    old_dmcc = {k: v for k, v in cfg["ventures"]["dmcc"].items()
                if k != "floor_exceptions"}
    cfg["ventures"] = {**cfg["ventures"], "floored": old_dmcc}
    monkeypatch.setattr(mr, "_CONFIG", cfg)
    provider, model_id, rationale = mr.pick_model("contract-review", venture="floored")
    assert provider == "ollama"
    assert "privacy floor" in rationale and "escalation" in rationale


def test_unknown_privacy_constraint_fails_closed():
    """Lean: unknown_constraint_rejected. `confidential` ranked 0 = no constraint."""
    with pytest.raises(ValueError, match="Unknown privacy class"):
        mr.pick_model("high-stakes", privacy_class="confidential")


def test_sensitive_task_never_escalates(monkeypatch):
    """Lean: pick_respects_all -- the task's own privacy level is a floor."""
    cfg = dict(mr._load_config())
    cfg["ventures"] = {**cfg["ventures"],
                       "leaky": {"default_class": "reasoning",
                                 "escalations": {"sensitive": "high-stakes"}}}
    monkeypatch.setattr(mr, "_CONFIG", cfg)
    provider, _, _ = mr.pick_model("sensitive", venture="leaky")
    assert provider == "ollama"


@pytest.mark.parametrize("venture", [None, "megaphone", "forge", "dmcc", "verity", "plur"])
@pytest.mark.parametrize("task", ["sensitive", "internal", "public-bulk", "reasoning",
                                  "high-stakes", "contract-review", "client-report",
                                  "architecture", "x"])
@pytest.mark.parametrize("pc", [None, "public-bulk", "internal", "sensitive"])
def test_any_private_floor_yields_a_local_model(venture, task, pc):
    """Exhaustive over the shipped routing.yaml: whenever some constraint is
    internal or stricter, the pick is local (ollama) -- never frontier."""
    cfg = mr._load_config()
    try:
        provider, _, _ = mr.pick_model(task, venture=venture, privacy_class=pc)
    except ValueError:
        return  # unknown task with no venture default: refused, not routed
    floors = [pc] if pc else []
    # Decision C3: a venture's listed floor_exceptions waive ITS floor only.
    if (venture and cfg["ventures"][venture].get("privacy_floor")
            and task not in (cfg["ventures"][venture].get("floor_exceptions") or [])):
        floors.append(cfg["ventures"][venture]["privacy_floor"])
    if task in cfg["privacy_levels"]:
        floors.append(task)
    need = max((cfg["privacy_levels"].index(f) for f in floors), default=0)
    if need >= cfg["privacy_levels"].index("internal"):
        assert provider == "ollama"
