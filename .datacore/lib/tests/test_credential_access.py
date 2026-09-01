"""Tests for credential_access.py — value resolution.

The multi-var tests here cover a defect found on 2026-09-01: `_entry` accepts
an id or any declared variable name, but both `resolve` and `get_value` then
discarded which variable was asked for and used the entry's primary. Every
multi-var credential served one value for all its members — 17 entries and 34
variables, including private keys and OAuth1 secrets.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
import credential_access as ca


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated index + assembled env, with distinct value per variable."""
    secrets = tmp_path / ".datacore" / "secrets"
    env = tmp_path / ".datacore" / "env"
    secrets.mkdir(parents=True)
    env.mkdir(parents=True)

    index = secrets / "credential-index.yaml"
    index.write_text(yaml.dump({
        "credentials": [
            {
                "id": "multi",
                "scope": "global",
                "var_name": "MULTI_KEY",
                "vars": ["MULTI_KEY", "MULTI_SECRET", "MULTI_ID"],
            },
            {
                "id": "no-primary",
                "scope": "global",
                "vars": ["NP_FIRST", "NP_SECOND"],
            },
            {
                "id": "solo",
                "scope": "global",
                "var_name": "SOLO_TOKEN",
            },
        ]
    }))
    (env / ".env").write_text(
        "MULTI_KEY=key-value\n"
        "MULTI_SECRET=secret-value\n"
        "MULTI_ID=id-value\n"
        "NP_FIRST=first-value\n"
        "NP_SECOND=second-value\n"
        "SOLO_TOKEN=solo-value\n"
    )

    monkeypatch.setattr(ca, "INDEX", index)
    monkeypatch.setattr(ca, "ENV", env)
    return tmp_path


class TestMultiVarResolution:
    def test_each_declared_var_serves_its_own_value(self, store):
        assert ca.get_value("MULTI_KEY") == "key-value"
        assert ca.get_value("MULTI_SECRET") == "secret-value"
        assert ca.get_value("MULTI_ID") == "id-value"

    def test_var_without_declared_primary_serves_itself(self, store):
        assert ca.get_value("NP_FIRST") == "first-value"
        assert ca.get_value("NP_SECOND") == "second-value"

    def test_lookup_by_id_serves_the_primary_var(self, store):
        assert ca.get_value("multi") == "key-value"

    def test_lookup_by_id_without_primary_serves_first_declared(self, store):
        assert ca.get_value("no-primary") == "first-value"


class TestSingleVarUnchanged:
    def test_by_var_name(self, store):
        assert ca.get_value("SOLO_TOKEN") == "solo-value"

    def test_by_id(self, store):
        assert ca.get_value("solo") == "solo-value"


class TestInstanceLocalOverride:
    def test_override_applies_to_the_overridden_var(self, store):
        (store / ".datacore" / "env" / "local.env").write_text("MULTI_KEY=local-key\n")
        assert ca.get_value("MULTI_KEY") == "local-key"

    def test_override_of_one_var_does_not_capture_its_siblings(self, store):
        """local.env defining the primary must not route siblings there.

        `resolve` checked local.env for the entry's primary variable and, on a
        hit, returned local.env for every variable in the entry — including
        ones local.env does not define, which then read as unresolvable.
        """
        (store / ".datacore" / "env" / "local.env").write_text("MULTI_KEY=local-key\n")
        assert ca.get_value("MULTI_SECRET") == "secret-value"
        assert ca.get_value("MULTI_ID") == "id-value"
