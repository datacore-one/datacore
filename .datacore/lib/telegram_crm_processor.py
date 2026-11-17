#!/usr/bin/env python3
"""
Telegram CRM Processor

Extracts contact relationship data from Telegram Desktop JSON export.
Designed to integrate with DIP-0012 CRM module.

Usage:
    python telegram_crm_processor.py /path/to/result.json [--output-dir ./output]
"""

import json
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import argparse
import re

# Configuration
OWNER_NAME = os.environ.get("DATACORE_USER_NAME", "owner").lower()
DORMANT_DAYS = 60  # Days without contact = dormant
HIGH_VALUE_MIN_MESSAGES = 50  # Minimum messages for high-value contact
RECENT_DAYS = 30  # What counts as recent interaction


def parse_date(date_str):
    """Parse Telegram date format."""
    if not date_str:
        return None
    try:
        # Format: 2025-12-17T10:30:45
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except:
        return None


def calculate_relationship_score(contact_data, now):
    """
    Calculate relationship score (0-1) based on DIP-0012 factors:
    - Recency (40%): Exponential decay from last interaction
    - Frequency (30%): Interactions per month
    - Depth (20%): Message volume indicates relationship depth
    - Reciprocity (10%): Two-way vs one-way
    """
    messages = contact_data.get('messages', [])
    if not messages:
        return 0.0

    # Recency score (40%)
    last_msg_date = parse_date(contact_data.get('last_message_date'))
    if last_msg_date:
        days_since = (now - last_msg_date).days
        recency_score = max(0, 1 - (days_since / 365))  # Decay over a year
    else:
        recency_score = 0

    # Frequency score (30%)
    first_msg = parse_date(messages[0].get('date')) if messages else None
    if first_msg and last_msg_date:
        months = max(1, (last_msg_date - first_msg).days / 30)
        msgs_per_month = len(messages) / months
        frequency_score = min(1, msgs_per_month / 50)  # 50 msgs/month = max
    else:
        frequency_score = 0

    # Depth score (20%)
    msg_count = len(messages)
    depth_score = min(1, msg_count / 500)  # 500+ msgs = max depth

    # Reciprocity score (10%)
    sent_count = sum(1 for m in messages if m.get('from', '').lower() == OWNER_NAME)
    received_count = len(messages) - sent_count
    if msg_count > 0:
        min_ratio = min(sent_count, received_count) / max(1, max(sent_count, received_count))
        reciprocity_score = min_ratio
    else:
        reciprocity_score = 0

    # Weighted score
    score = (
        recency_score * 0.4 +
        frequency_score * 0.3 +
        depth_score * 0.2 +
        reciprocity_score * 0.1
    )

    return round(score, 3)


def categorize_contact(contact_data, score, now):
    """Categorize contact by relationship status."""
    last_msg_date = parse_date(contact_data.get('last_message_date'))
    msg_count = contact_data.get('message_count', 0)

    if not last_msg_date:
        return 'unknown'

    days_since = (now - last_msg_date).days

    if days_since <= 14:
        return 'active'
    elif days_since <= 30:
        return 'warming'
    elif days_since <= 60:
        return 'cooling'
    else:
        return 'dormant'


def extract_contacts(data, now):
    """Extract and analyze all personal 1-1 chats."""
    chats = data.get('chats', {}).get('list', [])
    contacts = []

    for chat in chats:
        if chat.get('type') != 'personal_chat':
            continue

        name = chat.get('name', 'Unknown')
        if not name or name == 'None':
            continue

        messages = chat.get('messages', [])
        if not messages:
            continue

        # Get message dates
        first_msg_date = parse_date(messages[0].get('date')) if messages else None
        last_msg_date = parse_date(messages[-1].get('date')) if messages else None

        # Count sent vs received
        sent_count = sum(1 for m in messages if m.get('from', '').lower() == OWNER_NAME)
        received_count = len(messages) - sent_count

        # Get recent messages (last 5)
        recent_msgs = []
        for msg in messages[-5:]:
            text = msg.get('text', '')
            if isinstance(text, list):
                text = ' '.join(str(t.get('text', t) if isinstance(t, dict) else t) for t in text)
            if text and len(text) > 200:
                text = text[:200] + '...'
            recent_msgs.append({
                'date': msg.get('date', '')[:10],
                'from': msg.get('from', 'Unknown'),
                'text': text[:100] if text else ''
            })

        contact_data = {
            'name': name,
            'message_count': len(messages),
            'sent_count': sent_count,
            'received_count': received_count,
            'first_message_date': first_msg_date.isoformat() if first_msg_date else None,
            'last_message_date': last_msg_date.isoformat() if last_msg_date else None,
            'messages': messages,  # Keep for score calculation
            'recent_messages': recent_msgs
        }

        # Calculate score
        score = calculate_relationship_score(contact_data, now)
        status = categorize_contact(contact_data, score, now)

        # Remove full messages from output (too large)
        del contact_data['messages']

        contact_data['relationship_score'] = score
        contact_data['status'] = status
        contact_data['days_since_contact'] = (now - last_msg_date).days if last_msg_date else 999

        contacts.append(contact_data)

    return contacts


def extract_partnership_groups(data):
    """Extract partnership/project-related groups."""
    chats = data.get('chats', {}).get('list', [])
    partnerships = []

    # Patterns that indicate partnership groups
    partnership_patterns = [
        r'<>',      # "Organization <> X"
        r'x\s',     # "Project x Y"
        r'\|',      # "Project | Partner"
        r'&',       # "DF & Valicon"
    ]

    for chat in chats:
        chat_type = chat.get('type', '')
        name = chat.get('name', '')

        # Only interested in private supergroups/groups
        if chat_type not in ['private_group', 'private_supergroup']:
            continue

        # Check if name indicates partnership
        is_partnership = any(re.search(p, name, re.IGNORECASE) for p in partnership_patterns)

        # Also include groups with certain keywords
        keywords = ['organization', 'productx', 'bzz', 'partnerorg', 'fair data']
        has_keyword = any(kw in name.lower() for kw in keywords)

        if not (is_partnership or has_keyword):
            continue

        messages = chat.get('messages', [])
        last_msg_date = parse_date(messages[-1].get('date')) if messages else None

        partnerships.append({
            'name': name,
            'type': chat_type,
            'message_count': len(messages),
            'last_message': last_msg_date.isoformat() if last_msg_date else None,
            'is_partnership': is_partnership
        })

    return partnerships


def identify_reconnection_candidates(contacts, now, min_messages=HIGH_VALUE_MIN_MESSAGES):
    """
    Identify high-value dormant contacts worth reconnecting with.

    Criteria:
    - Significant history (50+ messages)
    - No contact in 60+ days
    - Had active relationship (score > 0.3 historically)
    """
    candidates = []

    for c in contacts:
        if c['message_count'] < min_messages:
            continue
        if c['status'] != 'dormant':
            continue
        if c['days_since_contact'] < DORMANT_DAYS:
            continue

        # Calculate historical engagement
        historical_score = c['relationship_score']

        candidates.append({
            'name': c['name'],
            'message_count': c['message_count'],
            'days_since_contact': c['days_since_contact'],
            'last_contact': c['last_message_date'][:10] if c.get('last_message_date') else 'Unknown',
            'historical_score': historical_score,
            'recent_messages': c.get('recent_messages', [])[:2],
            'priority': 'high' if c['message_count'] > 200 else 'medium'
        })

    # Sort by message count (history depth)
    candidates.sort(key=lambda x: x['message_count'], reverse=True)

    return candidates


def generate_followup_tasks(candidates, limit=20):
    """Generate org-mode tasks for reconnection."""
    tasks = []

    for c in candidates[:limit]:
        name = c['name']
        last_contact = c['last_contact']
        days = c['days_since_contact']
        priority = c['priority']

        # Determine scheduling based on priority
        if priority == 'high':
            schedule = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d %a')
        else:
            schedule = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %a')

        task = f"""* TODO Reconnect with [[{name}]] - {days} days since last contact :CRM:reconnect:
SCHEDULED: <{schedule}>
:PROPERTIES:
:CONTACT: {name}
:LAST_CONTACT: {last_contact}
:DAYS_DORMANT: {days}
:PRIORITY: {priority}
:END:
Last exchanged {c['message_count']} messages. Consider reaching out to maintain relationship.
"""
        tasks.append(task)

    return tasks


def generate_crm_contacts(contacts, output_dir):
    """Generate CRM contact note stubs for high-value contacts."""
    high_value = [c for c in contacts if c['message_count'] >= HIGH_VALUE_MIN_MESSAGES]

    contacts_dir = output_dir / 'contacts'
    contacts_dir.mkdir(exist_ok=True)

    created = []
    for c in high_value[:50]:  # Limit to top 50
        name = c['name']
        safe_name = re.sub(r'[^\w\s-]', '', name).strip()
        if not safe_name:
            continue

        filename = f"{safe_name}.md"
        filepath = contacts_dir / filename

        # Don't overwrite existing
        if filepath.exists():
            continue

        content = f"""---
type: contact
entity_type: person
name: "{name}"
status: draft
relationship_status: {c['status']}
relevance: 3
privacy: personal
channels:
  telegram: "@{safe_name.lower().replace(' ', '_')}"
source: telegram_export
telegram_stats:
  message_count: {c['message_count']}
  first_contact: {c['first_message_date'][:10] if c.get('first_message_date') else 'unknown'}
  last_contact: {c['last_message_date'][:10] if c.get('last_message_date') else 'unknown'}
  relationship_score: {c['relationship_score']}
created: {datetime.now().strftime('%Y-%m-%d')}
---

# {name}

## Overview

Contact imported from Telegram export. {c['message_count']} messages exchanged.

**Status:** {c['status'].title()}
**Last Contact:** {c['last_message_date'][:10] if c.get('last_message_date') else 'Unknown'}

## Notes

[Add notes about this contact]

## Goals

**What I want:**
- [Define your goals with this contact]

**What they want:**
- [What can you offer them?]

## Recent Context

Last messages:
"""

        for msg in c.get('recent_messages', [])[:3]:
            content += f"- {msg['date']}: {msg['from']}: {msg['text'][:80]}...\n"

        content += """
## Related

- [[Telegram Export December 2025]]
"""

        filepath.write_text(content)
        created.append(filename)

    return created


def main():
    parser = argparse.ArgumentParser(description='Process Telegram export for CRM')
    parser.add_argument('json_path', help='Path to result.json from Telegram export')
    parser.add_argument('--output-dir', default='./telegram_crm_output', help='Output directory')
    args = parser.parse_args()

    json_path = Path(args.json_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    print(f"Loading Telegram export from {json_path}...")
    with open(json_path, 'r') as f:
        data = json.load(f)

    now = datetime.now()

    # Extract contacts
    print("Extracting personal contacts...")
    contacts = extract_contacts(data, now)
    contacts.sort(key=lambda x: x['relationship_score'], reverse=True)

    # Extract partnerships
    print("Extracting partnership groups...")
    partnerships = extract_partnership_groups(data)
    partnerships.sort(key=lambda x: x['message_count'], reverse=True)

    # Identify reconnection candidates
    print("Identifying reconnection candidates...")
    candidates = identify_reconnection_candidates(contacts, now)

    # Generate outputs
    print("\nGenerating outputs...")

    # 1. Full contact analysis
    analysis = {
        'generated': now.isoformat(),
        'summary': {
            'total_contacts': len(contacts),
            'active': len([c for c in contacts if c['status'] == 'active']),
            'warming': len([c for c in contacts if c['status'] == 'warming']),
            'cooling': len([c for c in contacts if c['status'] == 'cooling']),
            'dormant': len([c for c in contacts if c['status'] == 'dormant']),
            'high_value': len([c for c in contacts if c['message_count'] >= HIGH_VALUE_MIN_MESSAGES]),
            'partnerships': len(partnerships),
            'reconnection_candidates': len(candidates)
        },
        'contacts': contacts[:100],  # Top 100 by score
        'partnerships': partnerships,
        'reconnection_candidates': candidates[:30]
    }

    analysis_path = output_dir / 'telegram_analysis.json'
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2, default=str)
    print(f"  - Analysis: {analysis_path}")

    # 2. Reconnection tasks (org-mode)
    tasks = generate_followup_tasks(candidates, limit=20)
    tasks_path = output_dir / 'reconnection_tasks.org'
    with open(tasks_path, 'w') as f:
        f.write("#+TITLE: Telegram Reconnection Tasks\n")
        f.write(f"#+DATE: {now.strftime('%Y-%m-%d')}\n\n")
        f.write("* Reconnection Queue\n\n")
        f.write('\n'.join(tasks))
    print(f"  - Tasks: {tasks_path}")

    # 3. CRM contact stubs
    created_contacts = generate_crm_contacts(contacts, output_dir)
    print(f"  - Contact stubs: {len(created_contacts)} created in {output_dir / 'contacts'}/")

    # 4. Summary report
    report = f"""# Telegram CRM Analysis Report

Generated: {now.strftime('%Y-%m-%d %H:%M')}

## Summary

| Metric | Count |
|--------|-------|
| Total 1-1 Contacts | {len(contacts)} |
| Active (< 14 days) | {analysis['summary']['active']} |
| Warming (14-30 days) | {analysis['summary']['warming']} |
| Cooling (30-60 days) | {analysis['summary']['cooling']} |
| Dormant (> 60 days) | {analysis['summary']['dormant']} |
| High-Value (50+ msgs) | {analysis['summary']['high_value']} |
| Partnership Groups | {len(partnerships)} |
| Reconnection Candidates | {len(candidates)} |

## Top 20 Contacts by Relationship Score

| Name | Messages | Score | Status | Last Contact |
|------|----------|-------|--------|--------------|
"""
    for c in contacts[:20]:
        report += f"| {c['name'][:25]} | {c['message_count']} | {c['relationship_score']:.2f} | {c['status']} | {c['last_message_date'][:10] if c.get('last_message_date') else 'N/A'} |\n"

    report += """
## Reconnection Priorities

High-value dormant contacts worth reconnecting with:

| Name | Messages | Days Dormant | Priority |
|------|----------|--------------|----------|
"""
    for c in candidates[:15]:
        report += f"| {c['name'][:25]} | {c['message_count']} | {c['days_since_contact']} | {c['priority']} |\n"

    report += """
## Partnership Groups (Active)

| Group | Type | Messages | Last Activity |
|-------|------|----------|---------------|
"""
    for p in partnerships[:20]:
        report += f"| {p['name'][:40]} | {'Partnership' if p['is_partnership'] else 'Internal'} | {p['message_count']} | {p['last_message'][:10] if p.get('last_message') else 'N/A'} |\n"

    report += f"""
## Files Generated

- `telegram_analysis.json` - Full analysis data
- `reconnection_tasks.org` - {len(tasks)} tasks for inbox.org
- `contacts/` - {len(created_contacts)} contact stubs

## Next Steps

1. Review reconnection candidates and prioritize outreach
2. Import tasks to `org/inbox.org`
3. Review generated contact stubs and enrich with context
4. Set up regular Telegram scan for ongoing CRM updates
"""

    report_path = output_dir / 'telegram_crm_report.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"  - Report: {report_path}")

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total contacts analyzed: {len(contacts)}")
    print(f"Active relationships: {analysis['summary']['active']}")
    print(f"Dormant (need attention): {analysis['summary']['dormant']}")
    print(f"Reconnection candidates: {len(candidates)}")
    print(f"Partnership groups: {len(partnerships)}")
    print(f"\nOutput directory: {output_dir}")


if __name__ == '__main__':
    main()
