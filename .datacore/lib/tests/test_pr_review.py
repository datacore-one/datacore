#!/usr/bin/env python3
"""Tests for pr_review.py pure functions."""

import unittest

from pr_review import build_review_prompt, parse_verdict


class TestBuildReviewPrompt(unittest.TestCase):
    """Tests for build_review_prompt()."""

    def _build(self, pr_types=None, diff='some diff', files=None, **kwargs):
        """Helper to build a prompt with defaults."""
        return build_review_prompt(
            repo=kwargs.get('repo', 'owner/repo'),
            pr_number=kwargs.get('pr_number', '42'),
            pr_title=kwargs.get('pr_title', 'Test PR'),
            pr_author=kwargs.get('pr_author', 'testuser'),
            pr_types=pr_types or [],
            diff=diff,
            files=files or ['file1.py'],
        )

    def test_module_type_instructions(self):
        prompt = self._build(pr_types=['module'])
        self.assertIn('MODULE', prompt)
        self.assertIn('module.yaml', prompt)
        self.assertIn('DIP-0016', prompt)

    def test_dip_type_instructions(self):
        prompt = self._build(pr_types=['dip'])
        self.assertIn('DIP', prompt)
        self.assertIn('DIP template', prompt)
        self.assertIn('feasibility', prompt)

    def test_agent_type_instructions(self):
        prompt = self._build(pr_types=['agent'])
        self.assertIn('AGENT', prompt)
        self.assertIn('DIP-0016 compliance', prompt)
        self.assertIn('Agent Context', prompt)

    def test_command_type_instructions(self):
        prompt = self._build(pr_types=['command'])
        self.assertIn('COMMAND', prompt)
        self.assertIn('registry compliance', prompt)
        self.assertIn('invocation patterns', prompt)

    def test_python_type_instructions(self):
        prompt = self._build(pr_types=['python'])
        self.assertIn('PYTHON', prompt)
        self.assertIn('security issues', prompt)

    def test_typescript_type_instructions(self):
        prompt = self._build(pr_types=['typescript'])
        self.assertIn('TYPESCRIPT', prompt)
        self.assertIn('types', prompt)
        self.assertIn('error handling', prompt)

    def test_includes_diff(self):
        prompt = self._build(diff='+ added line\n- removed line')
        self.assertIn('+ added line', prompt)
        self.assertIn('- removed line', prompt)

    def test_includes_metadata(self):
        prompt = self._build(
            repo='datacore-one/datacore',
            pr_number='99',
            pr_title='Fix the thing',
            pr_author='testuser',
            files=['src/main.py', 'tests/test_main.py'],
        )
        self.assertIn('#99', prompt)
        self.assertIn('datacore-one/datacore', prompt)
        self.assertIn('Fix the thing', prompt)
        self.assertIn('testuser', prompt)
        self.assertIn('src/main.py', prompt)
        self.assertIn('tests/test_main.py', prompt)

    def test_empty_types_uses_general(self):
        prompt = self._build(pr_types=[])
        self.assertIn('General documentation/config change', prompt)

    def test_multiple_types(self):
        prompt = self._build(pr_types=['module', 'python'])
        self.assertIn('MODULE', prompt)
        self.assertIn('PYTHON', prompt)

    def test_unknown_type_uses_general(self):
        prompt = self._build(pr_types=['unknown_thing'])
        self.assertIn('General documentation/config change', prompt)

    def test_diff_truncation(self):
        long_diff = 'x' * 60000
        prompt = self._build(diff=long_diff)
        # Should contain the truncation message
        self.assertIn('diff truncated', prompt)
        self.assertIn('10000 chars omitted', prompt)
        # Should contain first 50000 chars of the diff
        self.assertIn('x' * 1000, prompt)
        # Total prompt should not contain the full 60000-char diff
        self.assertLess(prompt.count('x'), 60000)

    def test_diff_at_limit_not_truncated(self):
        exact_diff = 'y' * 50000
        prompt = self._build(diff=exact_diff)
        self.assertNotIn('diff truncated', prompt)

    def test_diff_under_limit_not_truncated(self):
        short_diff = 'z' * 100
        prompt = self._build(diff=short_diff)
        self.assertNotIn('diff truncated', prompt)
        self.assertIn('z' * 100, prompt)


class TestParseVerdict(unittest.TestCase):
    """Tests for parse_verdict()."""

    def test_extracts_approve(self):
        text = "### Verdict\nAPPROVE\n### Complexity\nTRIVIAL\n### Quality\nEXEMPLARY"
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'APPROVE')
        self.assertEqual(result['complexity'], 'TRIVIAL')
        self.assertEqual(result['quality'], 'EXEMPLARY')

    def test_extracts_comment(self):
        text = "### Verdict\nCOMMENT\n### Complexity\nSTANDARD\n### Quality\nGOOD"
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'COMMENT')
        self.assertEqual(result['complexity'], 'STANDARD')
        self.assertEqual(result['quality'], 'GOOD')

    def test_extracts_request_changes(self):
        text = "### Verdict\nREQUEST_CHANGES\n### Complexity\nSIGNIFICANT\n### Quality\nNEEDS_WORK"
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'REQUEST_CHANGES')
        self.assertEqual(result['complexity'], 'SIGNIFICANT')
        self.assertEqual(result['quality'], 'NEEDS_WORK')

    def test_defaults_on_empty_text(self):
        result = parse_verdict('')
        self.assertEqual(result['verdict'], 'COMMENT')
        self.assertEqual(result['complexity'], 'STANDARD')
        self.assertEqual(result['quality'], 'GOOD')

    def test_defaults_on_garbage_text(self):
        result = parse_verdict('This is not a review at all. Random text.')
        self.assertEqual(result['verdict'], 'COMMENT')
        self.assertEqual(result['complexity'], 'STANDARD')
        self.assertEqual(result['quality'], 'GOOD')

    def test_partial_match_verdict_only(self):
        text = "### Summary\nLooks good.\n\n### Verdict\nAPPROVE\n\nSome other stuff."
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'APPROVE')
        # Others get defaults
        self.assertEqual(result['complexity'], 'STANDARD')
        self.assertEqual(result['quality'], 'GOOD')

    def test_partial_match_quality_only(self):
        text = "### Quality\nEXEMPLARY\n"
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'COMMENT')
        self.assertEqual(result['complexity'], 'STANDARD')
        self.assertEqual(result['quality'], 'EXEMPLARY')

    def test_handles_extra_whitespace(self):
        text = "###  Verdict \n  APPROVE\n###  Complexity \n  TRIVIAL\n###  Quality \n  EXEMPLARY"
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'APPROVE')
        self.assertEqual(result['complexity'], 'TRIVIAL')
        self.assertEqual(result['quality'], 'EXEMPLARY')

    def test_embedded_in_full_review(self):
        text = """### Summary
This PR adds a new module for handling X.

### Verdict
APPROVE

### Complexity
STANDARD

### Quality
GOOD

### Findings
- [file.py:10] Minor style issue

### Suggested Labels
- enhancement
"""
        result = parse_verdict(text)
        self.assertEqual(result['verdict'], 'APPROVE')
        self.assertEqual(result['complexity'], 'STANDARD')
        self.assertEqual(result['quality'], 'GOOD')


if __name__ == '__main__':
    unittest.main()
