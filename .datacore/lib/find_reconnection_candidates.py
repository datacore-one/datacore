#!/usr/bin/env python3
"""
Find high-value dormant contacts for reconnection.
"""

import os
import yaml
from pathlib import Path
from datetime import datetime

PEOPLE_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))) / "0-personal/contacts/people"

# Priority companies for reconnection
PRIORITY_COMPANIES = [
    "fundamental", "hashkey", "chainlink", "ondo", "wintermute", "gnosis",
    "hopr", "nym", "collider", "sevenx", "7x", "draper", "kraken", "flow traders",
    "brickken", "openeden", "gamma prime", "almanak", "mme", "binance", "gate",
    "lbank", "bity", "p2p", "socket", "jumper", "lifi", "bebop", "status",
    "ethereum", "polygon", "arbitrum", "optimism", "base", "solana", "avalanche"
]

def parse_contact(file_path):
    """Parse a contact markdown file."""
    try:
        with open(file_path, 'r') as f:
            content = f.read()

        # Extract YAML frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])
                return frontmatter
    except Exception as e:
        pass
    return None

def score_contact(contact):
    """Score a contact for reconnection priority."""
    score = 0
    reasons = []

    # Base relationship score (0-100 points)
    rel_score = contact.get('relationship_score', 0)
    score += rel_score * 100
    if rel_score > 0.5:
        reasons.append(f"High relationship score ({rel_score:.2f})")

    # Message count (0-50 points)
    stats = contact.get('telegram_stats', {})
    msg_count = stats.get('message_count', 0)
    score += min(msg_count * 2, 50)
    if msg_count > 20:
        reasons.append(f"Significant conversation ({msg_count} msgs)")

    # Recency bonus (more recent = better for reconnection)
    days_dormant = stats.get('days_dormant', 999)
    if days_dormant < 90:
        score += 30
        reasons.append("Recently active (<90 days)")
    elif days_dormant < 180:
        score += 20
        reasons.append("Semi-recent (3-6 months)")
    elif days_dormant < 365:
        score += 10
        reasons.append("Within past year")

    # Organization affiliation with priority companies
    org = contact.get('organization', '').lower()
    name = contact.get('name', '').lower()

    for company in PRIORITY_COMPANIES:
        if company in org or company in name:
            score += 50
            reasons.append(f"Priority company: {company}")
            break

    # Two-way communication (reciprocity)
    sent = stats.get('sent_count', 0)
    received = stats.get('received_count', 0)
    if sent > 0 and received > 0:
        score += 20
        reasons.append("Two-way communication")

    return score, reasons

def main():
    candidates = []

    for file_path in PEOPLE_DIR.glob("*.md"):
        contact = parse_contact(file_path)
        if not contact:
            continue

        # Only dormant contacts
        if contact.get('relationship_status') != 'dormant':
            continue

        # Skip contacts with very few messages
        stats = contact.get('telegram_stats', {})
        if stats.get('message_count', 0) < 3:
            continue

        score, reasons = score_contact(contact)

        # Minimum threshold
        if score < 30:
            continue

        candidates.append({
            'name': contact.get('name', 'Unknown'),
            'organization': contact.get('organization', ''),
            'score': score,
            'reasons': reasons,
            'msg_count': stats.get('message_count', 0),
            'days_dormant': stats.get('days_dormant', 0),
            'last_contact': stats.get('last_contact', ''),
            'file': file_path.name
        })

    # Sort by score
    candidates.sort(key=lambda x: x['score'], reverse=True)

    # Print top 50
    print("=" * 80)
    print("TOP RECONNECTION CANDIDATES FOR YEAR-END OUTREACH")
    print("=" * 80)
    print()

    # Group by priority
    tier1 = [c for c in candidates if c['score'] >= 100]
    tier2 = [c for c in candidates if 70 <= c['score'] < 100]
    tier3 = [c for c in candidates if 50 <= c['score'] < 70]

    print(f"## TIER 1: High Priority ({len(tier1)} contacts)")
    print("-" * 60)
    for c in tier1[:20]:
        org_str = f" @ {c['organization']}" if c['organization'] else ""
        print(f"  {c['name']}{org_str}")
        print(f"    Score: {c['score']:.0f} | Msgs: {c['msg_count']} | Dormant: {c['days_dormant']}d")
        print(f"    Why: {', '.join(c['reasons'][:3])}")
        print()

    print(f"\n## TIER 2: Medium Priority ({len(tier2)} contacts)")
    print("-" * 60)
    for c in tier2[:15]:
        org_str = f" @ {c['organization']}" if c['organization'] else ""
        print(f"  {c['name']}{org_str}")
        print(f"    Score: {c['score']:.0f} | Msgs: {c['msg_count']} | Dormant: {c['days_dormant']}d")
        print()

    print(f"\n## TIER 3: Opportunistic ({len(tier3)} contacts)")
    print("-" * 60)
    for c in tier3[:10]:
        org_str = f" @ {c['organization']}" if c['organization'] else ""
        print(f"  {c['name']}{org_str}")
        print(f"    Score: {c['score']:.0f} | Msgs: {c['msg_count']} | Dormant: {c['days_dormant']}d")
        print()

    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {len(tier1)} high priority, {len(tier2)} medium, {len(tier3)} opportunistic")
    print(f"Total candidates meeting threshold: {len(candidates)}")

if __name__ == "__main__":
    main()
