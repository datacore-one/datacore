"""
Tests for venture_loader.py — venture YAML schema validation and loading.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from venture_loader import (
    BudgetConfig,
    GitHubConfig,
    NightshiftConfig,
    RepoConfig,
    RoleConfig,
    VentureConfig,
    load_venture,
    load_venture_file,
    validate_venture,
)


MINIMAL_VENTURE = {
    "name": "forge",
    "description": "Autonomous digital product business",
    "stage": "discovery",
    "space": "4-forge",
    "autonomy": 1,
    "budget": {
        "ceiling": 100.0,
        "ai_tokens": 20.0,
        "real_spend": 10.0,
    },
}


class TestValidateVenture:
    def test_load_venture_from_dict(self):
        vc = load_venture(MINIMAL_VENTURE)
        assert vc.name == "forge"
        assert vc.stage == "discovery"
        assert vc.space == "4-forge"
        assert vc.autonomy == 1
        assert isinstance(vc.budget, BudgetConfig)
        assert vc.budget.ceiling == 100.0

    def test_validate_venture_missing_name(self):
        data = {**MINIMAL_VENTURE}
        del data["name"]
        errors = validate_venture(data)
        assert any("name" in e for e in errors)

    def test_validate_venture_invalid_stage(self):
        data = {**MINIMAL_VENTURE, "stage": "moonshot"}
        errors = validate_venture(data)
        assert any("stage" in e for e in errors)

    def test_validate_venture_autonomy_range(self):
        data = {**MINIMAL_VENTURE, "autonomy": 4}
        errors = validate_venture(data)
        assert any("autonomy" in e for e in errors)

    def test_validate_venture_budget_ceiling_gte_sum(self):
        data = {
            **MINIMAL_VENTURE,
            "budget": {"ceiling": 10.0, "ai_tokens": 8.0, "real_spend": 5.0},
        }
        errors = validate_venture(data)
        assert any("budget" in e for e in errors)

    def test_load_venture_with_roles(self):
        data = {
            **MINIMAL_VENTURE,
            "roles": {
                "operator": {
                    "description": "Does everything",
                    "cadences": {"daily": ["check-email"], "weekly": ["review"]},
                    "budget_authority": 10,
                },
                "cmo": {
                    "description": "Marketing",
                    "cadences": {"daily": ["social-check"]},
                    "budget_authority": 20,
                },
            },
        }
        vc = load_venture(data)
        assert len(vc.roles) == 2
        assert "operator" in vc.roles
        assert vc.roles["operator"].cadences["daily"] == ["check-email"]
        assert vc.roles["cmo"].budget_authority == 20

    def test_load_venture_from_file(self, tmp_path):
        venture_file = tmp_path / "venture.yaml"
        venture_file.write_text(yaml.dump(MINIMAL_VENTURE))
        vc = load_venture_file(venture_file)
        assert vc.name == "forge"
        assert vc.stage == "discovery"

    def test_load_venture_with_thesis(self):
        data = {**MINIMAL_VENTURE, "thesis": "Digital product automation at scale."}
        vc = load_venture(data)
        assert vc.thesis == "Digital product automation at scale."

    def test_load_venture_with_github(self):
        data = {
            **MINIMAL_VENTURE,
            "github": {
                "org": "datafund",
                "repos": [
                    {"name": "forge-engine", "role": "core"},
                    {"name": "forge-listings", "role": "output"},
                ],
            },
        }
        vc = load_venture(data)
        assert isinstance(vc.github, GitHubConfig)
        assert vc.github.org == "datafund"
        assert len(vc.github.repos) == 2
        assert vc.github.repos[0].name == "forge-engine"
