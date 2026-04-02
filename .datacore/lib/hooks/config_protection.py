#!/usr/bin/env python3
"""Config Protection Hook (PreToolUse: Edit, Write)

Blocks modifications to linter/formatter config files.
Agents frequently weaken configs to make checks pass instead of fixing code.

Exit codes:
  0 = allow (not a config file)
  2 = block (config file modification attempted)
"""
import json
import os
import sys

PROTECTED_BASENAMES = {
    # ESLint
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
    ".eslintrc.yml", ".eslintrc.yaml",
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
    "eslint.config.ts", "eslint.config.mts",
    # Prettier
    ".prettierrc", ".prettierrc.js", ".prettierrc.cjs", ".prettierrc.json",
    ".prettierrc.yml", ".prettierrc.yaml",
    "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs",
    # Biome
    "biome.json", "biome.jsonc",
    # Python linters
    ".ruff.toml", "ruff.toml",
    ".flake8", ".pylintrc", "setup.cfg",
    # Shell / Style / Markdown
    ".shellcheckrc", ".stylelintrc", ".stylelintrc.json",
    ".markdownlint.json", ".markdownlint.yaml", ".markdownlintrc",
    # Go
    ".golangci.yml", ".golangci.yaml",
    # Rust
    "clippy.toml", ".clippy.toml",
}

def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    file_path = (
        data.get("tool_input", {}).get("file_path", "")
        or data.get("tool_input", {}).get("file", "")
    )
    if not file_path:
        sys.stdout.write(raw)
        sys.exit(0)

    basename = os.path.basename(file_path)
    if basename in PROTECTED_BASENAMES:
        sys.stderr.write(
            f"BLOCKED: Modifying {basename} is not allowed. "
            "Fix the source code to satisfy linter/formatter rules instead of "
            "weakening the config. If this is a legitimate config change, "
            "the user can approve it manually.\n"
        )
        sys.exit(2)

    sys.stdout.write(raw)
    sys.exit(0)

if __name__ == "__main__":
    main()
