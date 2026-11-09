"""oauth_health_check names the token's agent from principals.yaml (INS-3)."""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import roster  # noqa: E402
import oauth_health_check as o  # noqa: E402


def test_the_row_names_the_installs_operations_agent(monkeypatch):
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "ops" if role == "chief of operations" else None)
    monkeypatch.setattr(roster, "display", lambda name, path=None: "Opsy")
    assert o._claude_row_name() == "claude_code_oauth (Opsy)"


def test_a_fresh_install_names_nobody(monkeypatch):
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: None)
    assert o._claude_row_name() == "claude_code_oauth"
