#!/usr/bin/env python3
"""
Integration tests — span multiple Datacore modules.

Tests:
1. Context merge pipeline: create temp layers → rebuild → verify composed output
2. Tag validator + tags.yaml: load real registry → validate known good/bad tags
3. Credential lifecycle: bootstrap → audit
"""

import os
import sys

import pytest

# Add lib to path
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..")))


class TestContextMergePipeline:
    """Integration: context_merge.py end-to-end with temp layers."""

    def test_rebuild_from_layers(self, tmp_path):
        """Create base + local layers, rebuild, verify composed output."""
        from context_merge import merge_context

        (tmp_path / "CLAUDE.base.md").write_text(
            "<!-- === Layer: BASE (PUBLIC) === -->\n# Base\nPublic content here.\n"
        )
        (tmp_path / "CLAUDE.local.md").write_text(
            "# Private\nLocal secrets here.\n"
        )

        content, warnings = merge_context(tmp_path, name="CLAUDE")

        assert "Public content here" in content
        assert "Local secrets here" in content

    def test_rebuild_without_local(self, tmp_path):
        """Rebuild should work with just a base layer."""
        from context_merge import merge_context

        (tmp_path / "CLAUDE.base.md").write_text(
            "<!-- === Layer: BASE (PUBLIC) === -->\n# Base Only\n"
        )

        content, warnings = merge_context(tmp_path, name="CLAUDE")

        assert "Base Only" in content

    def test_validate_catches_private_in_public(self, tmp_path):
        """Validation should warn about private content in public layers."""
        from context_merge import merge_context

        (tmp_path / "CLAUDE.base.md").write_text(
            "# Base\nContact me at user@example.com\n"
        )

        content, warnings = merge_context(tmp_path, name="CLAUDE", validate=True)
        assert len(warnings) > 0, "Should warn about email in public layer"


class TestTagValidatorIntegration:
    """Integration: tag_validator.py with real tags.yaml registry."""

    @pytest.fixture(autouse=True)
    def setup_validator(self):
        from tag_validator import TagValidator
        data_dir = os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))
        self.validator = TagValidator(data_dir)

    def test_loads_real_registry(self):
        """Tag validator should load the actual tags.yaml without errors."""
        assert len(self.validator.known_org_tags) > 0, "Should load org tags from registry"
        assert len(self.validator.known_org_tag_segments) > 0, "Should extract tag segments"

    def test_ai_compound_tags_valid(self):
        """AI compound tags should be recognized as valid."""
        valid_compounds = [
            ':AI:research:', ':AI:content:', ':AI:data:',
            ':AI:pm:', ':AI:technical:', ':AI:comms:',
        ]

        for tag in valid_compounds:
            segments = [s for s in tag.strip(':').split(':') if s]
            assert self.validator._all_segments_known(segments), \
                f"Compound tag {tag} should be valid"

    def test_unknown_tag_detected(self):
        """Truly unknown tags should be detected."""
        unknown_segments = ['xyzzy_nonexistent_tag_12345']
        assert not self.validator._all_segments_known(unknown_segments)

    def test_validate_sample_org_content(self):
        """Validate a sample org snippet against real registry."""
        sample = "*** TODO [#A] Test task :datacore:engineering:\n"
        tags = self.validator.find_org_tags(sample)
        assert any('datacore' in t for t in tags)


class TestCredentialLifecycle:
    """Integration: creds.py CredentialManager with temp directory."""

    def test_audit_empty_dir(self, tmp_path):
        """Audit should handle empty credential directory gracefully."""
        from creds import CredentialManager

        env_dir = tmp_path / ".datacore" / "env"
        env_dir.mkdir(parents=True)

        mgr = CredentialManager(str(tmp_path))
        result = mgr.cmd_audit()
        assert isinstance(result, int)
