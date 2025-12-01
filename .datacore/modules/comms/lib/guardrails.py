# .datacore/modules/comms/lib/guardrails.py
"""Content guardrails — validates text against voice profile rules.

Used by both autonomous and human-approved pipelines.
Loads rules from voice.yaml or manual configuration.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GuardrailResult:
    passed: bool
    violations: List[str] = field(default_factory=list)


class ContentGuardrails:
    def __init__(
        self,
        anti_patterns: List[str] = None,
        promo_patterns: List[str] = None,
        max_length: int = 280,
        max_exclamations: int = 1,
        max_hashtags: int = 2,
        allow_emoji: bool = True,
    ):
        self.anti_patterns = anti_patterns or []
        self.promo_patterns = promo_patterns or []
        self.max_length = max_length
        self.max_exclamations = max_exclamations
        self.max_hashtags = max_hashtags
        self.allow_emoji = allow_emoji

    def check(self, text: str) -> GuardrailResult:
        violations = []

        # Length
        if len(text) > self.max_length:
            violations.append(
                f"Exceeds max length: {len(text)} > {self.max_length}"
            )

        # Anti-patterns (case-insensitive, word-boundary matching)
        for pattern in self.anti_patterns:
            regex = r'\b' + re.escape(pattern) + r'\b'
            if re.search(regex, text, re.IGNORECASE):
                violations.append(f"Anti-pattern matched: '{pattern}'")

        # Promo patterns (word-boundary matching)
        for pattern in self.promo_patterns:
            regex = r'\b' + re.escape(pattern) + r'\b'
            if re.search(regex, text, re.IGNORECASE):
                violations.append(f"Promotional language: '{pattern}'")

        # Exclamation marks
        if text.count('!') > self.max_exclamations:
            violations.append(
                f"Too many exclamation marks: {text.count('!')} > {self.max_exclamations}"
            )

        # Hashtags
        hashtag_count = len(re.findall(r'#\w+', text))
        if hashtag_count > self.max_hashtags:
            violations.append(
                f"Too many hashtags: {hashtag_count} > {self.max_hashtags}"
            )

        # Emoji
        if not self.allow_emoji and self._has_emoji(text):
            violations.append("Emoji not allowed")

        return GuardrailResult(
            passed=len(violations) == 0,
            violations=violations,
        )

    @classmethod
    def from_voice_yaml(cls, path: str, platform: str = 'x_twitter') -> 'ContentGuardrails':
        """Load guardrails from a voice.yaml file."""
        import yaml
        with open(path) as f:
            voice = yaml.safe_load(f)

        anti_patterns = []
        phrases = voice.get('phrases', {})
        anti_patterns.extend(phrases.get('avoid', []))

        plat = voice.get('platforms', {}).get(platform, {})
        max_length = plat.get('max_length', 280)

        hashtag_str = str(plat.get('hashtags', '2'))
        max_hashtags = int(re.search(r'\d+', hashtag_str.split('-')[-1].split('_')[0]).group()) if re.search(r'\d+', hashtag_str) else 2

        tone = voice.get('voice', {}).get('tone', {})
        allow_emoji = plat.get('emojis', 'none') != 'none'

        return cls(
            anti_patterns=anti_patterns,
            max_length=max_length,
            max_hashtags=max_hashtags,
            allow_emoji=allow_emoji,
            max_exclamations=0 if not tone.get('hype', True) else 1,
        )

    @staticmethod
    def _has_emoji(text: str) -> bool:
        for char in text:
            if unicodedata.category(char) in ('So', 'Sk'):
                return True
        return False
