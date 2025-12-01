# .datacore/modules/comms/tests/test_guardrails.py
"""Tests for voice-based content guardrails."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestContentGuardrails:
    """Guardrails block content that violates voice rules."""

    def test_blocks_marketing_language(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(anti_patterns=[
            "Decentralized revolution",
            "To the moon",
        ])
        result = g.check("Join the Decentralized revolution today")
        assert not result.passed
        assert "Decentralized revolution" in result.violations[0]

    def test_blocks_exclamation_marks(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(max_exclamations=0)
        result = g.check("This is amazing!")
        assert not result.passed

    def test_blocks_over_length(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(max_length=280)
        result = g.check("x" * 281)
        assert not result.passed
        assert "length" in result.violations[0].lower()

    def test_allows_clean_content(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(
            anti_patterns=["hype", "moon"],
            max_length=280,
            max_exclamations=0,
        )
        result = g.check("Privacy by architecture, not by promise.")
        assert result.passed
        assert len(result.violations) == 0

    def test_blocks_emoji(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(allow_emoji=False)
        result = g.check("Great work \U0001f680")
        assert not result.passed

    def test_blocks_hashtags(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(max_hashtags=0)
        result = g.check("Privacy matters #web3 #crypto")
        assert not result.passed

    def test_allows_limited_hashtags(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(max_hashtags=2)
        result = g.check("Privacy matters #web3 #crypto")
        assert result.passed

    def test_blocks_self_promotion(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(promo_patterns=["try Fairdrop", "check out"])
        result = g.check("You should try Fairdrop for private file sharing")
        assert not result.passed

    def test_case_insensitive_matching(self):
        from guardrails import ContentGuardrails
        g = ContentGuardrails(anti_patterns=["WAGMI"])
        result = g.check("wagmi friends")
        assert not result.passed


class TestGuardrailsFromVoiceYaml:
    """Guardrails can be loaded from a voice.yaml file."""

    def test_loads_from_yaml(self, tmp_path):
        import yaml
        from guardrails import ContentGuardrails

        voice = {
            'voice': {
                'tone': {'hype': False, 'preachy': False},
            },
            'donts': [
                "Never use 'revolutionary' or 'disruptive'",
            ],
            'phrases': {
                'avoid': ['WAGMI', 'To the moon', 'NFA / DYOR'],
            },
            'platforms': {
                'x_twitter': {
                    'max_length': 280,
                    'hashtags': '1-2_max',
                }
            },
        }
        voice_file = tmp_path / "voice.yaml"
        voice_file.write_text(yaml.dump(voice))

        g = ContentGuardrails.from_voice_yaml(str(voice_file), platform='x_twitter')
        assert g.max_length == 280
        assert 'wagmi' in [p.lower() for p in g.anti_patterns]
