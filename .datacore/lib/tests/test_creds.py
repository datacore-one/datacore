"""Tests for creds.py credential management CLI."""

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from creds import CredentialIndex, CredentialManager


def _index_path(root: Path) -> Path:
    """The one place the tests learn where the index lives: from the manager."""
    return CredentialManager(data_dir=str(root)).index_path


def write_index(root: Path, data: dict):
    """Write a credential-index.yaml file where CredentialManager reads it.

    The index moved from .datacore/specs/ to .datacore/secrets/ (see
    CredentialManager.__init__); this helper kept writing the old location,
    so sixteen tests here failed on main and nobody noticed. Bind to the
    manager's own attribute so the two cannot drift again.
    """
    index_path = _index_path(root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w") as f:
        yaml.dump(data, f)


def write_example(root: Path):
    """Write a credential-index.yaml.example file."""
    specs = root / ".datacore" / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "credential-index.yaml.example").write_text("# Example template\n")


def make_env_file(root: Path, rel_path: str, content: str = "KEY=value\n"):
    """Create a .env file at a relative path."""
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


SAMPLE_INDEX = {
    "version": "1.0",
    "updated": "2026-03-04",
    "credentials": [
        {
            "id": "test-api-key",
            "name": "Test API Key",
            "type": "api_key",
            "security_tier": "medium",
            "category": "ai-services",
            "provider": "test-provider",
            "locations": [
                {"path": ".datacore/env/.env", "var_name": "TEST_API_KEY", "primary": True}
            ],
            "used_by": ["0-personal"],
            "description": "A test credential for unit tests",
        },
        {
            "id": "prod-db-url",
            "name": "Production Database URL",
            "type": "connection_string",
            "security_tier": "critical",
            "category": "database",
            "provider": "postgres",
            "locations": [
                {"path": "secrets-repo://db/prod.env", "var_name": "DATABASE_URL", "primary": True}
            ],
            "used_by": ["3-fds/2-projects/fairdrop"],
            "description": "Production PostgreSQL connection",
        },
        {
            "id": "analytics-key",
            "name": "PostHog Analytics Key",
            "type": "api_key",
            "security_tier": "low",
            "category": "analytics",
            "provider": "posthog",
            "locations": [
                {"path": ".datacore/env/analytics.env", "var_name": "POSTHOG_KEY", "primary": True}
            ],
            "used_by": [],
            "description": "Product analytics tracking",
        },
    ],
    "security_tiers": {
        "critical": {"description": "Highest sensitivity", "rotation": "quarterly"},
        "high": {"description": "High sensitivity", "rotation": "semi-annually"},
        "medium": {"description": "Medium sensitivity", "rotation": "annually"},
        "low": {"description": "Low sensitivity", "rotation": "as needed"},
    },
    "categories": {
        "ai-services": "LLM API keys",
        "database": "Database connections",
        "analytics": "Analytics platforms",
    },
}


class TestCredentialIndex:
    """Tests for CredentialIndex loading and querying."""

    def test_loads_credentials(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        assert len(idx.credentials) == 3

    def test_filter_by_category(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        result = idx.filter(category="ai-services")
        assert len(result) == 1
        assert result[0].id == "test-api-key"

    def test_filter_by_tier(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        result = idx.filter(tier="critical")
        assert len(result) == 1
        assert result[0].id == "prod-db-url"

    def test_filter_combined(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        result = idx.filter(category="analytics", tier="low")
        assert len(result) == 1
        assert result[0].id == "analytics-key"

    def test_filter_no_match(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        result = idx.filter(category="nonexistent")
        assert len(result) == 0

    def test_get_by_id(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        cred = idx.get("test-api-key")
        assert cred is not None
        assert cred.name == "Test API Key"
        assert cred.provider == "test-provider"

    def test_get_missing_returns_none(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        assert idx.get("nonexistent") is None

    def test_search_by_name(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        results = idx.search("posthog")
        assert len(results) == 1
        assert results[0].id == "analytics-key"

    def test_search_by_var_name(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        results = idx.search("DATABASE_URL")
        assert len(results) == 1
        assert results[0].id == "prod-db-url"

    def test_search_case_insensitive(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        results = idx.search("POSTGRESQL")
        assert len(results) == 1

    def test_did_you_mean(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        suggestions = idx.did_you_mean("test-api-ky")
        assert "test-api-key" in suggestions

    def test_locations_parsed(self, tmp_path):
        write_index(tmp_path, SAMPLE_INDEX)
        idx = CredentialIndex(_index_path(tmp_path))
        cred = idx.get("test-api-key")
        assert len(cred.locations) == 1
        assert cred.locations[0].primary is True
        assert cred.locations[0].var_name == "TEST_API_KEY"

    def test_extra_fields_captured(self, tmp_path):
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [{
            "id": "with-extra",
            "name": "Extra Fields",
            "type": "api_key",
            "security_tier": "low",
            "category": "test",
            "provider": "test",
            "locations": [],
            "address": "0xABC",
            "domain": "test.eth",
        }]
        write_index(tmp_path, data)
        idx = CredentialIndex(_index_path(tmp_path))
        cred = idx.get("with-extra")
        assert cred.extra["address"] == "0xABC"
        assert cred.extra["domain"] == "test.eth"


class TestCredentialManagerList:
    """Tests for the list command."""

    def test_list_all(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list()
        assert rc == 0
        out = capsys.readouterr().out
        assert "test-api-key" in out
        assert "prod-db-url" in out
        assert "3 credential(s)" in out

    def test_list_filtered(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list(tier="critical")
        assert rc == 0
        out = capsys.readouterr().out
        assert "prod-db-url" in out
        assert "1 credential(s)" in out

    def test_list_json(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list(fmt="json")
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 3
        assert data[0]["id"] == "test-api-key"

    def test_list_empty_filter(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list(category="nonexistent")
        assert rc == 0
        out = capsys.readouterr().out
        assert "No credentials match" in out

    def test_list_no_index(self, tmp_path, capsys):
        write_example(tmp_path)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list()
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out


class TestCredentialManagerShow:
    """Tests for the show command."""

    def test_show_existing(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_show("test-api-key")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Test API Key" in out
        assert "TEST_API_KEY" in out

    def test_show_missing_with_suggestion(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_show("test-api-ky")
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out
        assert "Did you mean" in out


class TestCredentialManagerSearch:
    """Tests for the search command."""

    def test_search_found(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_search("postgres")
        assert rc == 0
        out = capsys.readouterr().out
        assert "prod-db-url" in out

    def test_search_not_found(self, tmp_path, capsys):
        write_index(tmp_path, SAMPLE_INDEX)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_search("zzzznotexist")
        assert rc == 0
        out = capsys.readouterr().out
        assert "No credentials matching" in out


class TestCredentialManagerAudit:
    """Tests for the audit command."""

    def test_audit_clean(self, tmp_path, capsys):
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [{
            "id": "clean-cred",
            "name": "Clean",
            "type": "api_key",
            "security_tier": "low",
            "category": "test",
            "provider": "test",
            "locations": [
                {"path": ".datacore/env/.env", "var_name": "CLEAN", "primary": True}
            ],
        }]
        write_index(tmp_path, data)
        make_env_file(tmp_path, ".datacore/env/.env")
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        assert rc == 0
        out = capsys.readouterr().out
        assert "All checks passed" in out

    def test_audit_invalid_status(self, tmp_path, capsys):
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [{
            "id": "bad-status",
            "name": "Bad",
            "type": "api_key",
            "security_tier": "low",
            "category": "test",
            "provider": "test",
            "status": "bogus",
            "locations": [{"path": "x", "var_name": "X", "primary": True}],
        }]
        write_index(tmp_path, data)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        assert rc == 1
        out = capsys.readouterr().out
        assert "Invalid status" in out

    def test_audit_missing_file(self, tmp_path, capsys):
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [{
            "id": "missing-file",
            "name": "Missing",
            "type": "api_key",
            "security_tier": "low",
            "category": "test",
            "provider": "test",
            "locations": [
                {"path": ".datacore/env/missing.env", "var_name": "X", "primary": True}
            ],
        }]
        write_index(tmp_path, data)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        # warnings don't cause failure
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing" in out.lower()

    def test_audit_secrets_repo_skipped(self, tmp_path, capsys):
        """secrets-repo:// paths should not be checked for existence."""
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [{
            "id": "remote-cred",
            "name": "Remote",
            "type": "api_key",
            "security_tier": "critical",
            "category": "test",
            "provider": "test",
            "locations": [
                {"path": "secrets-repo://keys/prod.env", "var_name": "KEY", "primary": True}
            ],
        }]
        write_index(tmp_path, data)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        assert rc == 0
        out = capsys.readouterr().out
        assert "All checks passed" in out

    def test_audit_duplicate_ids(self, tmp_path, capsys):
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [
            {
                "id": "dupe",
                "name": "First",
                "type": "api_key",
                "security_tier": "low",
                "category": "test",
                "provider": "test",
                "locations": [{"path": "x", "var_name": "X", "primary": True}],
            },
            {
                "id": "dupe",
                "name": "Second",
                "type": "api_key",
                "security_tier": "low",
                "category": "test",
                "provider": "test",
                "locations": [{"path": "y", "var_name": "Y", "primary": True}],
            },
        ]
        write_index(tmp_path, data)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        assert rc == 1
        out = capsys.readouterr().out
        assert "Duplicate" in out

    def test_audit_no_locations(self, tmp_path, capsys):
        data = dict(SAMPLE_INDEX)
        data["credentials"] = [{
            "id": "no-loc",
            "name": "No Location",
            "type": "api_key",
            "security_tier": "low",
            "category": "test",
            "provider": "test",
            "locations": [],
        }]
        write_index(tmp_path, data)
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        assert rc == 1
        out = capsys.readouterr().out
        assert "No storage locations" in out


class TestBootstrap:
    """Tests for bootstrap behavior when index is missing."""

    def test_bootstrap_message(self, tmp_path, capsys):
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list()
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out
        assert "credential-index.yaml" in out


class TestEmptyIndex:
    """Tests for edge case of empty credentials list."""

    def test_empty_credentials(self, tmp_path, capsys):
        write_index(tmp_path, {"version": "1.0", "credentials": []})
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_list()
        assert rc == 0
        out = capsys.readouterr().out
        assert "No credentials match" in out

    def test_audit_empty(self, tmp_path, capsys):
        write_index(tmp_path, {"version": "1.0", "credentials": []})
        mgr = CredentialManager(data_dir=str(tmp_path))
        rc = mgr.cmd_audit()
        assert rc == 0
