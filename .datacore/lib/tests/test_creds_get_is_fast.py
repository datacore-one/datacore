"""`creds get` must not pay for a full provider verification on every call.

The Claude login's verifier is a real `claude -p` session (~36 s on the mac,
measured 2026-09-27). Every `creds get claude-code-oauth` paid it, so the
agent-eval harness's 60 s broker limit timed out before any eval ran.

The fix caches a SUCCESSFUL verification only, for a short TTL, keyed by the
index id, the variable served and the value's sha256 -- never the value. A
changed value, an expired entry, an n-a or a FAIL all go back to the provider.
The cache file is owner-only.

Everything runs on a tmp HOME, tmp index and store with FAKE values; the
verifier is a counting stub, so this runs offline and deterministically.
"""

import io
import stat
import time
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
import credential_access as ca  # noqa: E402
import creds  # noqa: E402

FAKE = "fake-claude-oauth-value"
TTL = getattr(creds, "VERIFY_CACHE_TTL_S", 600)


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DATACORE_INSTANCE", "test-host")
    monkeypatch.delenv("DATACORE_CREDS_VERIFY_TTL", raising=False)
    secrets = tmp_path / ".datacore" / "secrets"
    env = tmp_path / ".datacore" / "env"
    secrets.mkdir(parents=True)
    env.mkdir(parents=True)
    index = secrets / "credential-index.yaml"
    index.write_text(yaml.dump({"credentials": [
        {"id": "claude-code-oauth", "name": "c", "var_name": "CLAUDE_CODE_OAUTH_TOKEN"},
    ]}))
    store = env / ".env"
    store.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={FAKE}\n")
    monkeypatch.setattr(ca, "INDEX", index)
    monkeypatch.setattr(ca, "ENV", env)
    monkeypatch.setattr(ca, "SECRETS", secrets)
    monkeypatch.setattr(ca, "DATA", tmp_path)
    monkeypatch.setattr(ca, "attest", lambda *a, **k: None)

    mgr = creds.CredentialManager(data_dir=str(tmp_path))
    mgr.verdict = ("ok", "stub accepted it")
    mgr.probes = []

    def slow_verifier(var, value, **k):
        # Stands in for the ~36 s `claude -p` probe: counted, never slept.
        mgr.probes.append((var, value))
        return mgr.verdict
    monkeypatch.setattr(ca, "verify_value", slow_verifier)

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    mgr.clock = clock
    mgr.store = store
    mgr.home = tmp_path
    return mgr


def _get(mgr, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mgr.cmd_get("claude-code-oauth", consumer="test", **k)
    return rc, out.getvalue(), err.getvalue()


def test_a_second_get_does_not_repeat_the_provider_verification(broker):
    rc1, out1, _ = _get(broker)
    rc2, out2, _ = _get(broker)
    assert (rc1, out1.strip()) == (0, FAKE)
    assert (rc2, out2.strip()) == (0, FAKE)
    assert len(broker.probes) == 1, "the slow provider verification ran on every get"


def test_the_cache_holds_a_fingerprint_never_the_value_and_is_owner_only(broker):
    _get(broker)
    files = [p for p in (broker.home / ".datacore").rglob("*verif*") if p.is_file()]
    assert files, "no verification cache written"
    for f in files:
        assert FAKE not in f.read_text()
        assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_a_changed_value_is_verified_again(broker):
    _get(broker)
    broker.store.write_text("CLAUDE_CODE_OAUTH_TOKEN=another-fake-value\n")
    rc, out, _ = _get(broker)
    assert rc == 0 and out.strip() == "another-fake-value"
    assert len(broker.probes) == 2


def test_an_expired_verification_is_redone(broker):
    _get(broker)
    broker.clock["now"] += TTL + 1
    _get(broker)
    assert len(broker.probes) == 2


def test_a_verification_from_the_future_is_not_trusted(broker):
    _get(broker)
    broker.clock["now"] -= 3600
    _get(broker)
    assert len(broker.probes) == 2


def test_a_failure_is_never_cached_as_success(broker):
    broker.verdict = ("FAIL", "HTTP 401")
    rc1, out1, _ = _get(broker)
    rc2, out2, _ = _get(broker)
    assert (rc1, out1, rc2, out2) == (1, "", 1, "")
    assert len(broker.probes) == 2


def test_na_is_never_cached_and_strict_still_refuses_it(broker):
    broker.verdict = ("n-a", "probe failed: TimeoutExpired")
    rc1, _, err1 = _get(broker)
    assert rc1 == 0 and creds.NA_NOTICE_PREFIX in err1
    rc2, out2, _ = _get(broker, strict=True)
    assert rc2 == 3 and out2 == ""
    assert len(broker.probes) == 2


def test_a_later_failure_drops_the_cached_success(broker):
    _get(broker)
    broker.clock["now"] += TTL + 1
    broker.verdict = ("FAIL", "HTTP 401")
    assert _get(broker)[0] == 1
    broker.verdict = ("ok", "stub accepted it")
    broker.clock["now"] += 1
    _get(broker)
    assert len(broker.probes) == 3


def test_ttl_zero_disables_the_cache(broker, monkeypatch):
    monkeypatch.setenv("DATACORE_CREDS_VERIFY_TTL", "0")
    _get(broker)
    _get(broker)
    assert len(broker.probes) == 2


def test_a_corrupt_cache_means_verify_again(broker):
    _get(broker)
    for f in (broker.home / ".datacore").rglob("*verif*"):
        if f.is_file():
            f.write_text("{not json")
    rc, out, _ = _get(broker)
    assert rc == 0 and out.strip() == FAKE
    assert len(broker.probes) == 2


def test_doctor_still_asks_the_provider_every_time(broker):
    _get(broker)
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        broker.cmd_doctor("claude-code-oauth")
    assert len(broker.probes) == 2


def test_the_claude_probe_is_isolated_and_cannot_be_answered_by_another_key(monkeypatch):
    """The verifier loads no user settings, hooks or MCP servers (that was ~36 s
    of the probe), and no other Anthropic credential rides along with the token
    under test. A 401 is still FAIL."""
    import shutil
    import subprocess
    seen = {}

    class _R:
        stdout = '{"is_error": false, "result": "OK"}'
        stderr = ""

    def fake_run(cmd, **k):
        seen["cmd"], seen["env"] = cmd, k["env"]
        return _R()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-other-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fake-other-token")
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ca.verify_value("CLAUDE_CODE_OAUTH_TOKEN", FAKE)[0] == "ok"
    assert {"--strict-mcp-config", "--no-session-persistence"} <= set(seen["cmd"])
    assert seen["cmd"][seen["cmd"].index("--setting-sources") + 1] == ""
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == FAKE
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "ANTHROPIC_AUTH_TOKEN" not in seen["env"]

    _R.stdout = '{"is_error": true, "result": "API Error: 401 OAuth access token is invalid."}'
    assert ca.verify_value("CLAUDE_CODE_OAUTH_TOKEN", FAKE)[0] == "FAIL"
