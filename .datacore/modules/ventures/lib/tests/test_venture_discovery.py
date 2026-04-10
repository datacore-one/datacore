"""
Tests for venture_discovery.py — finding all venture spaces in Datacore.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from venture_discovery import VentureSpace, discover_ventures, default_templates_dir


# Minimal valid venture dict matching venture_loader.py schema
MINIMAL_VENTURE = {
    "name": "test",
    "description": "A test venture",
    "stage": "discovery",
    "space": "4-forge",
    "autonomy": 0,
    "budget": {
        "ceiling": 100.0,
        "ai_tokens": 80.0,
        "real_spend": 20.0,
    },
    "nightshift": {
        "enabled": True,
    },
}


def write_venture_yaml(space_dir: Path, data: dict) -> Path:
    """Write a venture.yaml file into a space directory."""
    space_dir.mkdir(parents=True, exist_ok=True)
    venture_file = space_dir / "venture.yaml"
    venture_file.write_text(yaml.dump(data))
    return venture_file


class TestDiscoverVentures:
    def test_discover_ventures_finds_spaces(self, tmp_path):
        """A numbered space dir with a valid venture.yaml is found."""
        space_dir = tmp_path / "4-forge"
        write_venture_yaml(space_dir, {**MINIMAL_VENTURE, "space": "4-forge"})

        results = discover_ventures(tmp_path)

        assert len(results) == 1
        assert results[0].name == "4-forge"
        assert results[0].space_dir == space_dir
        assert results[0].config.name == "test"

    def test_discover_ventures_skips_invalid(self, tmp_path):
        """A space with broken YAML is silently skipped."""
        space_dir = tmp_path / "4-forge"
        space_dir.mkdir(parents=True, exist_ok=True)
        (space_dir / "venture.yaml").write_text("not: valid: yaml: [\nbad")

        results = discover_ventures(tmp_path)

        assert results == []

    def test_discover_ventures_multiple(self, tmp_path):
        """Multiple spaces are returned sorted by space number."""
        for spec in [
            ("6-meridian", "meridian"),
            ("2-analytics", "analytics"),
            ("4-forge", "forge"),
        ]:
            dir_name, venture_name = spec
            write_venture_yaml(
                tmp_path / dir_name,
                {**MINIMAL_VENTURE, "name": venture_name, "space": dir_name},
            )

        results = discover_ventures(tmp_path)

        assert len(results) == 3
        assert results[0].name == "2-analytics"
        assert results[1].name == "4-forge"
        assert results[2].name == "6-meridian"

    def test_discover_ventures_respects_nightshift_enabled(self, tmp_path):
        """nightshift_only=True skips ventures with nightshift.enabled=False."""
        # Nightshift-enabled venture
        write_venture_yaml(
            tmp_path / "4-forge",
            {**MINIMAL_VENTURE, "space": "4-forge", "nightshift": {"enabled": True}},
        )
        # Nightshift-disabled venture
        write_venture_yaml(
            tmp_path / "6-meridian",
            {**MINIMAL_VENTURE, "space": "6-meridian", "nightshift": {"enabled": False}},
        )

        results = discover_ventures(tmp_path, nightshift_only=True)

        assert len(results) == 1
        assert results[0].name == "4-forge"
