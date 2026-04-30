"""Tests for model_routing.py — covers acceptance criteria."""

import pytest
from model_routing import pick_model, list_classes, list_ventures, _load_config


class TestBasicClassLookup:
    """AC: basic class lookup."""

    def test_sensitive_returns_local_model(self):
        provider, model_id, _ = pick_model("sensitive")
        assert provider == "ollama"
        assert "qwen" in model_id or "llama" in model_id

    def test_high_stakes_returns_premium(self):
        provider, model_id, _ = pick_model("high-stakes")
        assert provider in ("anthropic", "openai", "google", "moonshot")

    def test_public_bulk_returns_cheap(self):
        provider, model_id, _ = pick_model("public-bulk")
        assert provider in ("google", "anthropic", "openai")
        assert "flash" in model_id or "haiku" in model_id or "mini" in model_id

    def test_all_classes_resolve(self):
        classes = list_classes()
        assert len(classes) >= 5
        for cls in classes:
            provider, model_id, rationale = pick_model(cls)
            assert provider
            assert model_id
            assert rationale


class TestVentureOverride:
    """AC: venture override."""

    def test_forge_escalation(self):
        # product-design escalates to reasoning for forge
        _, model_id, rationale = pick_model("product-design", venture="forge")
        assert "reasoning" in rationale

    def test_dmcc_privacy_floor(self):
        # dmcc has privacy_floor=internal, so public-bulk should bump up
        provider, model_id, rationale = pick_model("public-bulk", venture="dmcc")
        # Should use internal-class models, not public
        assert "privacy floor" in rationale or "internal" in rationale

    def test_megaphone_default(self):
        # Unknown task kind falls back to default_class
        _, _, rationale = pick_model("unknown-task", venture="megaphone")
        assert "default" in rationale


class TestPrivacyConstraint:
    """AC: privacy hard-constraint enforcement."""

    def test_sensitive_constraint_forces_local(self):
        provider, model_id, rationale = pick_model("high-stakes", privacy_class="sensitive")
        assert provider == "ollama"
        assert "privacy constraint" in rationale

    def test_internal_constraint_blocks_public(self):
        provider, model_id, rationale = pick_model("public-bulk", privacy_class="internal")
        # Should bump to internal models
        config = _load_config()
        internal_models = config["classes"]["internal"]["models"]
        assert any(model_id in m for m in internal_models)

    def test_no_constraint_uses_class_directly(self):
        _, _, rationale = pick_model("reasoning")
        assert "privacy constraint" not in rationale


class TestMissingVentureFallback:
    """AC: missing-venture fallback."""

    def test_unknown_venture_uses_class_directly(self):
        provider, model_id, rationale = pick_model("reasoning", venture="nonexistent-venture")
        assert "not found" in rationale
        # Should still return a valid model
        assert provider in ("anthropic", "google", "openai", "moonshot")


class TestMissingClassError:
    """AC: missing-class error."""

    def test_unknown_class_raises(self):
        with pytest.raises(ValueError) as exc_info:
            pick_model("totally-fake-class")
        assert "Unknown task class" in str(exc_info.value)


class TestConfigStructure:
    """Validate routing.yaml meets spec."""

    def test_minimum_five_classes(self):
        classes = list_classes()
        assert len(classes) >= 5
        required = {"sensitive", "internal", "public-bulk", "reasoning", "high-stakes"}
        assert required.issubset(set(classes))

    def test_minimum_three_ventures(self):
        ventures = list_ventures()
        assert len(ventures) >= 3

    def test_ventures_have_required_fields(self):
        config = _load_config()
        for name, v in config["ventures"].items():
            assert "default_class" in v, f"{name} missing default_class"


class TestRationaleLogging:
    """AC: every decision logged with rationale."""

    def test_rationale_always_present(self):
        _, _, rationale = pick_model("reasoning")
        assert rationale and len(rationale) > 0

    def test_rationale_explains_venture_lookup(self):
        _, _, rationale = pick_model("content-strategy", venture="megaphone")
        assert "megaphone" in rationale

    def test_rationale_explains_privacy_override(self):
        _, _, rationale = pick_model("high-stakes", privacy_class="sensitive")
        assert "privacy" in rationale.lower()
