"""sprint_sync: which claimers are agents is the install's setting (INS-3)."""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import roster  # noqa: E402
import sprint_sync  # noqa: E402


def test_the_install_names_its_agent_claimers(monkeypatch):
    monkeypatch.setattr(roster, "section", lambda key, path=None:
                        {"agent_claimer": "ops-bot|runner"} if key == "sprint_sync" else {})
    rx = sprint_sync._agent_claimer()
    assert rx.search("OPS-BOT-on-host") and rx.search("runner")
    assert not rx.search("a-person")


def test_without_a_setting_only_a_self_declared_agent_counts(monkeypatch):
    monkeypatch.setattr(roster, "section", lambda key, path=None: {})
    rx = sprint_sync._agent_claimer()
    assert rx.search("some-agent") and rx.search("nightshift")
    assert not rx.search("ops-bot") and not rx.search("a-person")
