"""first_run greets as the install's own chief of staff, never ours (INS-3)."""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import roster  # noqa: E402
import first_run  # noqa: E402


def test_the_installer_given_name_wins(monkeypatch):
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "cos")
    assert first_run._brief({"cosName": "Ada"}).count("You are Ada,") == 1


def test_the_registry_names_the_chief_of_staff(monkeypatch):
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "cos" if role == "chief of staff" else None)
    monkeypatch.setattr(roster, "display", lambda name, path=None: "Cosy")
    assert "You are Cosy," in first_run._brief({})


def test_a_fresh_install_uses_a_neutral_name(monkeypatch):
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: None)
    assert f"You are {first_run.DEFAULT_COS_NAME}," in first_run._brief({})
