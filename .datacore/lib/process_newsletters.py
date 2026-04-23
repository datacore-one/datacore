#!/usr/bin/env python3
"""Process newsletter backlog by topic groups. Extract insights for active projects."""
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

DATA_DIR = Path(os.environ.get('DATA_DIR', Path.home() / 'Data'))
PERSONAL = DATA_DIR / '0-personal'
REPORTS_DIR = PERSONAL / 'content' / 'reports'
TODAY = date.today().isoformat()

# Load env
for ef in [Path.home() / 'config' / 'nightshift.env', DATA_DIR / '.datacore' / 'env' / '.env']:
    if ef.exists():
        for line in ef.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                if k not in os.environ and k != 'ANTHROPIC_API_KEY':
                    os.environ[k] = v


GROUPS = [
    {
        'name': 'Verity & Data Economy',
        'tags': ['verity', 'rwa', 'privacy-tech', 'data-economy', 'erc3643'],
        'projects': 'Verity (data marketplace + tokenization), Datafund (fair data economy), Dubai pilot, Santorio',
        'filename': 'newsletter-verity-data-economy',
    },
    {
        'name': 'AI & Technology',
        'tags': ['ai', 'tech', 'openai'],
        'projects': 'Datacore (AI second brain), PLUR (engram memory for AI agents), ADE (agent data exchange)',
        'filename': 'newsletter-ai-technology',
    },
    {
        'name': 'Crypto & Trading',
        'tags': ['crypto', 'trading'],
        'projects': 'Meridian (hedge fund), Trading bots (turtle/momentum/HMM/BZZ), Hyperliquid positions',
        'filename': 'newsletter-crypto-trading',
    },
    {
        'name': 'Strategy & Business',
        'tags': ['strategy', 'management', 'pitchdeck', 'product', 'work'],
        'projects': 'Ventures framework (Forge, Meridian, Megaphone), Datafund fundraising, client consulting',
        'filename': 'newsletter-strategy-business',
    },
    {
        'name': 'Health & Personal',
        'tags': ['health', 'stoicism', 'teo'],
        'projects': 'Health module (Oura/HealthKit tracking), longevity research, personal development',
        'filename': 'newsletter-health-personal',
    },
    {
        'name': 'Datafund & Swarm',
        'tags': ['datafund', 'swarm', 'fairdrop'],
        'projects': 'Datafund (fair data economy), FDS (Fair Data Society), Fairdrop, Swarm storage',
        'filename': 'newsletter-datafund-swarm',
    },
]


def load_newsletter_items():
    """Load all newsletter items from backlog."""
    backlog = PERSONAL / 'org' / 'research_backlog_2026-04.md'
    content = backlog.read_text()
    nl_match = re.search(r'## Newsletter Reading Queue \(\d+ items\)\n\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if not nl_match:
        return []
    return [l.strip() for l in nl_match.group(1).strip().split('\n') if l.strip().startswith('- ')]


def group_items(items, group):
    """Filter items matching group tags."""
    matched = []
    for item in items:
        item_lower = item.lower()
        for tag in group['tags']:
            if ':' + tag + ':' in item_lower or ':' + tag in item_lower:
                matched.append(item)
                break
    return matched


def process_group(group, items):
    """Send group items to Claude for analysis."""
    titles = '\n'.join(items[:200])  # Cap at 200 items per prompt

    prompt = f"""You are analyzing {len(items)} newsletter article titles/links collected over 4 months (Dec 2025 - Apr 2026).

ACTIVE PROJECTS: {group['projects']}

TOPIC GROUP: {group['name']}

ARTICLE TITLES:
{titles}

Analyze these for relevance to the active projects listed above. Produce a structured report:

## Executive Summary
2-3 sentences: what themes emerge, how relevant is this body of reading to active projects.

## Key Themes (top 5-7)
For each theme:
- **Theme name**: 1-2 sentence description
- **Relevance**: Which project this matters for and why
- **Signal strength**: How many articles touch this theme

## Opportunities Identified
Concrete opportunities for the active projects based on patterns in these articles. Be specific — name technologies, companies, standards, or market shifts that could be leveraged.

## Action Items
5-10 specific next steps. Format as org-mode tasks:
```
* TODO [#A/B/C] Task description :tag:
```

## Industry Landscape Updates
Key companies, products, or standards mentioned that should be tracked. Format as a list with one-line descriptions.

## Articles Worth Reading
From the titles, identify the 5-10 most valuable articles that should actually be read in full (not just skimmed as titles). Explain why for each.

Output the report in markdown. Be analytical and specific to the projects listed.
"""

    try:
        result = subprocess.run(
            ['claude', '-p', '--dangerously-skip-permissions', '--output-format', 'text', prompt],
            cwd=DATA_DIR, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            print(f"  Claude error: {result.stderr[:200]}")
            return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def main():
    print(f"Loading newsletter items...")
    all_items = load_newsletter_items()
    print(f"Total: {len(all_items)} items")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    used = set()
    action_items = []

    for group in GROUPS:
        items = group_items(all_items, group)
        # Also grab untagged items based on title keywords for the first pass
        print(f"\n{'='*60}")
        print(f"## {group['name']} — {len(items)} items")
        print(f"{'='*60}")

        if len(items) < 3:
            print(f"  Too few items, skipping")
            continue

        for item in items:
            used.add(item)

        print(f"  Processing with Claude...")
        report = process_group(group, items)

        if report:
            report_path = REPORTS_DIR / f"{group['filename']}-{TODAY}.md"
            header = f"# {group['name']} — Newsletter Analysis\n\n"
            header += f"**Date**: {TODAY}\n"
            header += f"**Items analyzed**: {len(items)}\n"
            header += f"**Projects**: {group['projects']}\n\n---\n\n"
            report_path.write_text(header + report)
            print(f"  Report: {report_path.name}")

            # Extract action items
            for line in report.split('\n'):
                if line.strip().startswith('* TODO'):
                    action_items.append(line.strip())
        else:
            print(f"  FAILED")

    # Remaining unmatched items
    unmatched = [i for i in all_items if i not in used]
    print(f"\n{'='*60}")
    print(f"Unmatched items (no group tags): {len(unmatched)}")
    print(f"{'='*60}")

    if unmatched and len(unmatched) > 10:
        print(f"  Processing unmatched as 'General' group...")
        general_group = {
            'name': 'General / Untagged',
            'tags': [],
            'projects': 'All ventures — looking for cross-cutting opportunities',
            'filename': 'newsletter-general',
        }
        report = process_group(general_group, unmatched[:200])
        if report:
            report_path = REPORTS_DIR / f"newsletter-general-{TODAY}.md"
            header = f"# General / Untagged — Newsletter Analysis\n\n"
            header += f"**Date**: {TODAY}\n"
            header += f"**Items analyzed**: {len(unmatched)}\n\n---\n\n"
            report_path.write_text(header + report)
            print(f"  Report: {report_path.name}")

    # Write action items to inbox
    if action_items:
        inbox = PERSONAL / 'org' / 'inbox.org'
        existing = inbox.read_text() if inbox.exists() else ""
        new_items = "\n".join(action_items) + "\n"
        inbox.write_text(existing + "\n" + new_items)
        print(f"\n{len(action_items)} action items added to inbox.org")

    print(f"\nDone! Reports in {REPORTS_DIR}")


if __name__ == '__main__':
    main()
