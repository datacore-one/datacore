"""Shared disposable identity fixtures; state isolation is in .datacore/conftest.py."""
import sys
from pathlib import Path

import pytest

# `lib` on the path once, instead of 96 of the 212 files doing it themselves and
# the rest relying on datacore-core.pth -- a file that exists on a developer
# machine and on no CI runner. That difference is why a test could pass here and
# fail there, or the reverse: test_missing_library_fails_open spent its whole
# life unable to make `tool_policy` absent because the .pth kept it importable.
# Stating the dependency here makes the suite say the same thing in both places.
LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


@pytest.fixture
def briefing_principals(tmp_path, monkeypatch):
    """Exercise real policy checks with declared, disposable identities."""
    import actor_identity
    import ledger.policy
    registry = tmp_path / "principals.yaml"
    registry.write_text("principals:\n  human: {kind: human}\n  worker: {kind: agent}\n  agent: {kind: agent}\n  t: {kind: agent}\n")
    policy = tmp_path / "approvals.yaml"
    policy.write_text("version: 1\napprover: human\ncosign_effects: [email.send, payment, prod.deploy]\nprincipals:\n  worker: {}\n  agent: {}\n  t: {}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", policy)


@pytest.fixture
def install_roster(tmp_path_factory, monkeypatch):
    """A declared install roster for drill tests that delegate as named agents.

    `DelegationDrill.delegation_space` enrols THIS install's principals and
    their `may_delegate_to` from the gitignored `registry/principals.yaml` and
    `config/approvals_policy.local.yaml` (`delegation_drill._install_roster`).
    Tests that delegate as `winston` and dispatch as `miles` therefore passed
    only on a host whose private registry happens to declare those names, and
    failed everywhere else -- every CI checkout -- with "unregistered writer
    'winston'". They test the claim loop, not this installation, so they get a
    scratch registry and policy of their own and say the same thing anywhere.
    """
    import delegation_drill
    d = tmp_path_factory.mktemp("install-roster")
    registry = d / "principals.yaml"
    registry.write_text(
        "principals:\n"
        "  gregor: {kind: human, writes_as: [gregor-laptop]}\n"
        "  winston: {kind: agent, writes_as: [winston, bridge]}\n"
        "  miles: {kind: agent, writes_as: [miles, nightshift]}\n")
    policy = d / "approvals_policy.yaml"
    policy.write_text(
        "version: 1\napprover: human\ncosign_effects: [email.send, payment, prod.deploy]\n"
        "principals:\n"
        "  gregor: {}\n"
        "  winston: {may_delegate_to: [miles]}\n"
        "  miles: {may_delegate_to: [winston]}\n")
    monkeypatch.setattr(delegation_drill, "_INSTALL_PRINCIPALS", registry)
    monkeypatch.setattr(delegation_drill, "_INSTALL_POLICY", policy)
    return registry, policy


# ── promise gate (evals first) ───────────────────────────────────────────────
# A red-by-design promise eval is collected only by the scoreboard
# (PROMISE_EVALS_ALL=1); ordinary runs collect it once its promise is green in
# the committed baseline, so a regression then fails here. See promise_gate.py.
def pytest_ignore_collect(collection_path, config):
    import importlib.util as _ilu
    from pathlib import Path as _P
    try:
        _root = _P(__file__).resolve().parents[3]
    except IndexError:
        _root = _P('/nonexistent')
    _gate = _root / ".datacore" / "lib" / "promise_gate.py"
    if not _gate.exists():
        return None if "test_promise_" not in _P(str(collection_path)).name else True
    _spec = _ilu.spec_from_file_location("promise_gate", _gate)
    _m = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return True if _m.should_ignore(_P(str(collection_path))) else None
