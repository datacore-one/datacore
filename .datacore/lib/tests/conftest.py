"""Shared disposable identity fixtures; state isolation is in .datacore/conftest.py."""
import pytest


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
