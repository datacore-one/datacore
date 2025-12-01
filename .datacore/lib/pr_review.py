#!/usr/bin/env python3
"""
PR Review via Nightshift.

Triggered by GitHub Actions SSH. Fetches PR diff, dispatches to appropriate
Datacore review agent, and posts results back as a PR review comment.

Usage:
    python3 pr_review.py --repo owner/name --pr 42 --types "agent,dip" --title "PR title" --author "username"
"""

import argparse
import base64
import os
import re
import subprocess
from pathlib import Path

DATA_DIR = Path(os.environ.get('DATA_DIR', os.path.expanduser('~/Data')))


def fetch_pr_diff(repo: str, pr_number: str) -> str:
    """Fetch PR diff using gh CLI."""
    result = subprocess.run(
        ['gh', 'pr', 'diff', pr_number, '--repo', repo],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f'Failed to fetch PR diff: {result.stderr}')
    return result.stdout


def fetch_pr_files(repo: str, pr_number: str) -> list:
    """Fetch list of changed files."""
    result = subprocess.run(
        ['gh', 'pr', 'view', pr_number, '--repo', repo,
         '--json', 'files', '--jq', '.files[].path'],
        capture_output=True, text=True, timeout=30
    )
    return [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]


def build_review_prompt(repo: str, pr_number: str, pr_title: str,
                        pr_author: str, pr_types: list, diff: str,
                        files: list) -> str:
    """Build the prompt for Claude to review the PR."""
    type_instructions = []
    for pr_type in pr_types:
        if pr_type == 'module':
            type_instructions.append(
                '- This PR modifies a MODULE. Check module.yaml validity, '
                'agent definitions follow DIP-0016, and commands are properly registered.'
            )
        elif pr_type == 'dip':
            type_instructions.append(
                '- This PR contains a DIP. Validate it follows the DIP template, '
                'check for conflicts with existing DIPs, and assess feasibility.'
            )
        elif pr_type == 'agent':
            type_instructions.append(
                '- This PR modifies AGENT definitions. Check DIP-0016 compliance, '
                'verify Agent Context section exists, and assess prompt quality.'
            )
        elif pr_type == 'command':
            type_instructions.append(
                '- This PR modifies COMMAND definitions. Check registry compliance '
                'and verify invocation patterns.'
            )
        elif pr_type == 'python':
            type_instructions.append(
                '- This PR contains PYTHON code. Check for security issues, '
                'code quality, and consistency with existing patterns.'
            )
        elif pr_type == 'typescript':
            type_instructions.append(
                '- This PR contains TYPESCRIPT code. Check types, '
                'error handling, and consistency with existing patterns.'
            )

    type_block = '\n'.join(type_instructions) if type_instructions else '- General documentation/config change.'

    # Truncate diff if too large
    max_diff_len = 50000
    diff_truncated = diff[:max_diff_len]
    if len(diff) > max_diff_len:
        diff_truncated += f'\n\n... (diff truncated, {len(diff) - max_diff_len} chars omitted)'

    return f"""You are reviewing PR #{pr_number} on {repo}.

**Title:** {pr_title}
**Author:** {pr_author}
**Changed files:** {', '.join(files)}

## Review Instructions

{type_block}

## General Review Criteria

1. **Privacy**: No personal data, secrets, or absolute paths
2. **Consistency**: Follows existing patterns and conventions
3. **Quality**: Well-written, clear purpose, no unnecessary complexity
4. **DIP compliance**: Changes align with relevant DIPs
5. **YAGNI**: No over-engineering or unnecessary features

## Output Format

Provide your review in this exact format:

### Summary
One paragraph assessment.

### Verdict
APPROVE | COMMENT | REQUEST_CHANGES

### Complexity
TRIVIAL | STANDARD | SIGNIFICANT

### Quality
EXEMPLARY | GOOD | NEEDS_WORK

### Findings
- [file:line] Finding description (if any)

### Suggested Labels
- label1, label2

---

## PR Diff

```diff
{diff_truncated}
```
"""


def run_review(prompt: str) -> str:
    """Run the review via Claude CLI, piping prompt via stdin."""
    result = subprocess.run(
        ['claude', '-p'],
        input=prompt,
        capture_output=True, text=True, timeout=300,
        cwd=str(DATA_DIR)
    )
    return result.stdout


def post_review_comment(repo: str, pr_number: str, review_body: str):
    """Post review as a PR comment."""
    subprocess.run(
        ['gh', 'pr', 'comment', pr_number, '--repo', repo, '--body', review_body],
        check=True, timeout=30
    )


def apply_labels(repo: str, pr_number: str, labels: list):
    """Apply labels to the PR."""
    for label in labels:
        subprocess.run(
            ['gh', 'pr', 'edit', pr_number, '--repo', repo, '--add-label', label],
            timeout=15
        )


def parse_verdict(review_text: str) -> dict:
    """Extract structured data from the review."""
    result = {
        'verdict': 'COMMENT',
        'complexity': 'STANDARD',
        'quality': 'GOOD',
    }

    verdict_match = re.search(r'###\s*Verdict\s*\n\s*(APPROVE|COMMENT|REQUEST_CHANGES)', review_text)
    if verdict_match:
        result['verdict'] = verdict_match.group(1)

    complexity_match = re.search(r'###\s*Complexity\s*\n\s*(TRIVIAL|STANDARD|SIGNIFICANT)', review_text)
    if complexity_match:
        result['complexity'] = complexity_match.group(1)

    quality_match = re.search(r'###\s*Quality\s*\n\s*(EXEMPLARY|GOOD|NEEDS_WORK)', review_text)
    if quality_match:
        result['quality'] = quality_match.group(1)

    return result


def main():
    parser = argparse.ArgumentParser(description='Review a PR via Nightshift')
    parser.add_argument('--repo', required=True, help='GitHub repo (owner/name)')
    parser.add_argument('--pr', required=True, help='PR number')
    parser.add_argument('--types', default='', help='Comma-separated PR types')
    parser.add_argument('--title', default='', help='PR title')
    parser.add_argument('--title-b64', default='', help='PR title (base64 encoded)')
    parser.add_argument('--author', default='', help='PR author')
    args = parser.parse_args()

    title = args.title
    if args.title_b64:
        title = base64.b64decode(args.title_b64).decode('utf-8')

    pr_types = [t.strip() for t in args.types.split(',') if t.strip()]

    print(f'Reviewing PR #{args.pr} on {args.repo}')
    print(f'Types: {pr_types}')

    # Fetch PR data
    diff = fetch_pr_diff(args.repo, args.pr)
    files = fetch_pr_files(args.repo, args.pr)

    # Build and run review
    prompt = build_review_prompt(
        args.repo, args.pr, title, args.author, pr_types, diff, files
    )
    review_text = run_review(prompt)

    # Parse verdict
    verdict = parse_verdict(review_text)

    # Format comment
    comment = f"""## Nightshift PR Review

{review_text}

---
*Automated review by [Datacore Nightshift](https://github.com/datacore-one/datacore-nightshift)*
*Verdict: {verdict['verdict']} | Complexity: {verdict['complexity']} | Quality: {verdict['quality']}*
"""

    # Post comment and labels
    post_review_comment(args.repo, args.pr, comment)

    labels = [f"complexity:{verdict['complexity'].lower()}"]
    if verdict['quality'] == 'EXEMPLARY':
        labels.append('exemplary')
    apply_labels(args.repo, args.pr, labels)

    print(f'Review posted. Verdict: {verdict["verdict"]}')


if __name__ == '__main__':
    main()
