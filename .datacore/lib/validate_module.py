#!/usr/bin/env python3
"""
Module skeleton validation utility.

Validates that a Datacore module has complete structure with all required files,
no placeholders, and proper content sections.

Usage:
    python validate_module.py <module_path>

Exit codes:
    0 - Validation passed
    1 - Validation failed
"""

import yaml
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


class ModuleValidator:
    """Validates Datacore module skeleton completeness."""

    def __init__(self, module_path: Path):
        self.module_path = Path(module_path)
        self.issues = []
        self.checks_passed = 0
        self.checks_total = 0

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Run all validation checks.

        Returns:
            Tuple of (passed: bool, issues: List[str])
        """
        print(f"Validating module at: {self.module_path}\n")

        self.check_required_files()
        self.check_content_structure()
        self.check_placeholders()

        return len(self.issues) == 0, self.issues

    def _check(self, condition: bool, success_msg: str, failure_msg: str):
        """Record a check result."""
        self.checks_total += 1
        if condition:
            self.checks_passed += 1
            print(f"  [✓] {success_msg}")
        else:
            print(f"  [✗] {failure_msg}")
            self.issues.append(failure_msg)

    def check_required_files(self):
        """Verify required files exist and have content."""
        print("Required Files Check:")

        # Check core required files
        required = {
            'module.yaml': 'Module manifest',
            'CLAUDE.base.md': 'AI context (public layer)',
            '.gitignore': 'Git ignore rules',
        }

        for file, desc in required.items():
            path = self.module_path / file
            if not path.exists():
                self._check(False, f"{desc} exists", f"Missing required file: {file}")
            elif path.stat().st_size == 0:
                self._check(False, f"{desc} has content", f"Empty file: {file}")
            else:
                self._check(True, f"{desc} exists ({path.stat().st_size} bytes)", "")

        # Parse module.yaml to check provides section
        yaml_path = self.module_path / 'module.yaml'
        if not yaml_path.exists():
            print("\n⚠️  Cannot validate provides section - module.yaml missing\n")
            return

        try:
            with open(yaml_path) as f:
                config = yaml.safe_load(f)

            provides = config.get('provides', {})

            # Check commands
            for cmd in provides.get('commands', []):
                cmd_file = self.module_path / 'commands' / f'{cmd}.md'
                self._check(
                    cmd_file.exists() and cmd_file.stat().st_size > 50,
                    f"Command {cmd} exists with content",
                    f"Command file missing or empty: commands/{cmd}.md"
                )

            # Check skills
            for skill in provides.get('skills', []):
                skill_file = self.module_path / 'skills' / skill / 'SKILL.md'
                self._check(
                    skill_file.exists() and skill_file.stat().st_size > 50,
                    f"Skill {skill} exists with content",
                    f"Skill file missing or empty: skills/{skill}/SKILL.md"
                )

            # Check agents
            for agent in provides.get('agents', []):
                agent_file = self.module_path / 'agents' / f'{agent}.md'
                self._check(
                    agent_file.exists() and agent_file.stat().st_size > 50,
                    f"Agent {agent} exists with content",
                    f"Agent file missing or empty: agents/{agent}.md"
                )

        except yaml.YAMLError as e:
            self._check(False, "module.yaml is valid YAML", f"Invalid YAML in module.yaml: {e}")
        except Exception as e:
            self._check(False, "module.yaml is readable", f"Error reading module.yaml: {e}")

        print()

    def check_content_structure(self):
        """Verify required sections exist in files."""
        print("Content Structure Check:")

        # Check module.yaml has required fields
        yaml_path = self.module_path / 'module.yaml'
        if yaml_path.exists():
            try:
                with open(yaml_path) as f:
                    config = yaml.safe_load(f)

                required_fields = ['name', 'version', 'description', 'author']
                for field in required_fields:
                    self._check(
                        field in config and config[field],
                        f"module.yaml has {field}",
                        f"module.yaml missing required field: {field}"
                    )

                # Check provides section exists
                self._check(
                    'provides' in config and config['provides'],
                    "module.yaml has provides section",
                    "module.yaml missing provides section"
                )

            except yaml.YAMLError:
                pass  # Already reported in check_required_files
            except Exception as e:
                self._check(False, "module.yaml is valid", f"Error parsing module.yaml: {e}")

        # Check commands have required sections
        commands_dir = self.module_path / 'commands'
        if commands_dir.exists():
            for cmd_file in commands_dir.glob('*.md'):
                content = cmd_file.read_text()
                cmd_name = cmd_file.stem

                # Check for Workflow section
                has_workflow = '## Workflow' in content or '### Step 1:' in content
                self._check(
                    has_workflow,
                    f"{cmd_name} has Workflow section",
                    f"{cmd_file.name}: Missing Workflow section"
                )

                # Check for Your Boundaries section
                has_boundaries = '## Your Boundaries' in content
                self._check(
                    has_boundaries,
                    f"{cmd_name} has Your Boundaries section",
                    f"{cmd_file.name}: Missing Your Boundaries section"
                )

                # Check for Error Handling section
                has_errors = '## Error Handling' in content
                self._check(
                    has_errors,
                    f"{cmd_name} has Error Handling section",
                    f"{cmd_file.name}: Missing Error Handling section"
                )

        # Check skills have required sections
        skills_dir = self.module_path / 'skills'
        if skills_dir.exists():
            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue

                skill_file = skill_dir / 'SKILL.md'
                if skill_file.exists():
                    content = skill_file.read_text()
                    skill_name = skill_dir.name

                    # Check for Instructions section
                    has_instructions = '## Instructions' in content
                    self._check(
                        has_instructions,
                        f"Skill {skill_name} has Instructions section",
                        f"skills/{skill_name}/SKILL.md: Missing Instructions section"
                    )

        # Check CLAUDE.base.md documents the module
        claude_file = self.module_path / 'CLAUDE.base.md'
        if claude_file.exists():
            content = claude_file.read_text()

            # Should have module description
            has_description = len(content) > 100
            self._check(
                has_description,
                "CLAUDE.base.md has content",
                "CLAUDE.base.md appears incomplete (< 100 chars)"
            )

        print()


    def _strip_code_blocks(self, text: str) -> str:
        """Strip fenced and inline code blocks before placeholder scanning."""
        # Remove fenced blocks (``` ... ```)
        text = re.sub(r'```[\s\S]*?```', '', text)
        # Remove inline code (`...`) but not bare backticks
        text = re.sub(r'`[^`\n]+`', '', text)
        return text

    def check_placeholders(self):
        """Detect placeholder text in module files."""
        print("Placeholder Detection:")

        placeholder_patterns = [
            r'TODO',
            r'FIXME',
            r'\[Replace\s+this\]',
            r'\[Add\s+\w+\s+here\]',
            r'\[Fill.*?\]',
            r'<name>',
            r'<description>',
            r'<author>',
            r'<command>',
            r'<skill>',
            r'<agent>',
        ]

        pattern = re.compile('|'.join(placeholder_patterns), re.IGNORECASE)
        placeholders_found = []

        # Check markdown files
        for file in self.module_path.rglob('*.md'):
            if '.git' in str(file):
                continue

            try:
                raw = file.read_text()
                content = self._strip_code_blocks(raw)
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count('\n') + 1
                    rel_path = file.relative_to(self.module_path)
                    placeholders_found.append(
                        f"{rel_path}:{line_num}: Placeholder '{match.group()}'"
                    )
            except Exception as e:
                print(f"  ⚠️  Error reading {file}: {e}")

        # Check module.yaml
        yaml_path = self.module_path / 'module.yaml'
        if yaml_path.exists():
            try:
                content = yaml_path.read_text()
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count('\n') + 1
                    placeholders_found.append(
                        f"module.yaml:{line_num}: Placeholder '{match.group()}'"
                    )
            except Exception as e:
                print(f"  ⚠️  Error reading module.yaml: {e}")

        if placeholders_found:
            self._check(
                False,
                "No placeholders found",
                f"Found {len(placeholders_found)} placeholder(s)"
            )
            for placeholder in placeholders_found:
                print(f"    - {placeholder}")
                self.issues.append(placeholder)
        else:
            self._check(True, "No placeholders found", "")

        print()

    def print_summary(self, passed: bool):
        """Print validation summary."""
        print("=" * 60)
        print(f"VALIDATION RESULT: {'PASS' if passed else 'FAIL'}")
        print("=" * 60)
        print(f"\nChecks: {self.checks_passed}/{self.checks_total} passed")
        print(f"Issues: {len(self.issues)}\n")

        if passed:
            print("✓ Module skeleton is complete and ready for registration.")
            print("\nAll required files exist, content is complete, no placeholders found.")
            print("Safe to proceed to registration.")
        else:
            print("✗ VALIDATION FAILED\n")
            print("Fix the following issues before registration:\n")
            for i, issue in enumerate(self.issues, 1):
                print(f"{i}. {issue}")
            print("\n⚠️  Cannot proceed to registration until validation passes.")


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_module.py <module_path>")
        print("\nValidates Datacore module skeleton completeness.")
        print("Checks for required files, content structure, and placeholders.")
        sys.exit(1)

    module_path = Path(sys.argv[1])
    if not module_path.exists():
        print(f"Error: Module path does not exist: {module_path}")
        sys.exit(1)

    if not module_path.is_dir():
        print(f"Error: Module path is not a directory: {module_path}")
        sys.exit(1)

    print("=" * 60)
    print(f"MODULE SKELETON VALIDATION: {module_path.name}")
    print("=" * 60)
    print()

    validator = ModuleValidator(module_path)
    passed, issues = validator.validate()
    validator.print_summary(passed)

    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
