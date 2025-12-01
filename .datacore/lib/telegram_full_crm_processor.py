#!/usr/bin/env python3
"""
Telegram Full CRM Processor

Comprehensive processor for Telegram export to CRM contacts.
- Creates ALL person contacts
- Extracts companies from contact names
- Copies photos when available
- Links people to companies
- Prepares company manifest for research

Usage:
    python telegram_full_crm_processor.py /path/to/result.json

Output: Creates contacts in 0-personal/contacts/
"""

import json
import sys
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Configuration
OWNER_NAME = os.environ.get("DATACORE_USER_NAME", "owner").lower()

# Paths
DATA_ROOT = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
CONTACTS_DIR = DATA_ROOT / '0-personal' / 'contacts'
PEOPLE_DIR = CONTACTS_DIR / 'people'
COMPANIES_DIR = CONTACTS_DIR / 'companies'
IMAGES_DIR = PEOPLE_DIR / 'images'
LANDSCAPE_DIR = CONTACTS_DIR / 'landscape'

# Company extraction patterns
COMPANY_PATTERNS = [
    r'\|\s*(.+)$',           # Name | Company
    r'@\s*(.+)$',            # Name @ Company
    r'-\s+(.+)$',            # Name - Company
    r'\(([^)]+)\)$',         # Name (Company)
    r'from\s+(.+)$',         # Name from Company
]

# Known company suffixes for detection
COMPANY_SUFFIXES = ['Labs', 'Network', 'Protocol', 'DAO', 'Capital', 'Ventures',
                    'Foundation', 'Inc', 'Corp', 'Ltd', 'GmbH', 'AG', 'Finance',
                    'Tech', 'Studio', 'Group', 'Partners', 'Fund', 'VC', 'Bank']

# Industry keywords for categorization
INDUSTRY_KEYWORDS = {
    'defi': ['defi', 'swap', 'lending', 'yield', 'liquidity', 'dex', 'amm'],
    'infrastructure': ['infra', 'node', 'validator', 'rpc', 'data', 'storage', 'ipfs'],
    'privacy': ['privacy', 'zk', 'zkp', 'fhe', 'mpc', 'encrypted', 'anonymous'],
    'gaming': ['game', 'gaming', 'nft', 'metaverse', 'play'],
    'investment': ['capital', 'ventures', 'vc', 'fund', 'invest', 'portfolio'],
    'exchange': ['exchange', 'trading', 'trade', 'market', 'cex', 'kraken', 'binance', 'coinbase'],
    'media': ['media', 'news', 'content', 'press', 'journalism'],
    'research': ['research', 'labs', 'academic', 'university'],
    'legal': ['legal', 'law', 'compliance', 'regulatory'],
    'ai': ['ai', 'ml', 'machine learning', 'gpt', 'llm', 'model'],
    'rwa': ['rwa', 'real world', 'tokenization', 'asset'],
    'social': ['social', 'community', 'dao', 'governance'],
}


def parse_date(date_str):
    """Parse Telegram date format."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except:
        return None


def extract_company_from_name(name):
    """Extract company name from contact name."""
    if not name:
        return None, name

    for pattern in COMPANY_PATTERNS:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            company = match.group(1).strip()
            # Clean person name by removing the company part
            person_name = re.sub(pattern, '', name, flags=re.IGNORECASE).strip()
            # Validate it looks like a company
            if len(company) > 2 and (
                any(suffix.lower() in company.lower() for suffix in COMPANY_SUFFIXES) or
                company[0].isupper()
            ):
                return company, person_name

    return None, name


def guess_industries(name, company=None):
    """Guess industries based on name/company keywords."""
    industries = []
    text = f"{name} {company or ''}".lower()

    for industry, keywords in INDUSTRY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            industries.append(industry)

    return industries if industries else ['crypto']  # Default to crypto


def calculate_relationship_score(msg_count, days_since, sent_count, received_count):
    """Calculate relationship score (0-1)."""
    # Simple scoring based on volume and recency
    volume_score = min(1, msg_count / 500)
    recency_score = max(0, 1 - (days_since / 365))

    # Reciprocity
    total = sent_count + received_count
    if total > 0:
        min_ratio = min(sent_count, received_count) / max(1, max(sent_count, received_count))
        reciprocity_score = min_ratio
    else:
        reciprocity_score = 0

    return round(volume_score * 0.4 + recency_score * 0.4 + reciprocity_score * 0.2, 3)


def get_status_from_days(days_since):
    """Get relationship status based on days since last contact."""
    if days_since <= 14:
        return 'active'
    elif days_since <= 30:
        return 'warming'
    elif days_since <= 60:
        return 'cooling'
    else:
        return 'dormant'


def find_chat_photos(chat_folder, telegram_export_dir):
    """Find photos in a chat folder."""
    photos = []
    chat_path = telegram_export_dir / 'chats' / chat_folder / 'photos'
    if chat_path.exists():
        for f in chat_path.iterdir():
            if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif']:
                photos.append(f)
    return photos


def safe_filename(name):
    """Create safe filename from contact name."""
    # Remove special characters, keep spaces and alphanumeric
    safe = re.sub(r'[^\w\s-]', '', name).strip()
    safe = re.sub(r'\s+', '-', safe)  # Convert spaces to hyphens
    return safe if safe else 'unknown'


def create_person_contact(contact_data, output_dir, images_dir):
    """Create a person contact markdown file."""
    name = contact_data['name']
    safe_name = safe_filename(name)

    # Don't overwrite existing
    filepath = output_dir / f"{safe_name}.md"
    if filepath.exists():
        return None, 'exists'

    company = contact_data.get('company')
    company_link = f"[[{company}]]" if company else ''

    industries = contact_data.get('industries', ['crypto'])
    industries_yaml = ', '.join(industries)

    # Handle image
    image_link = ''
    if contact_data.get('photo_path'):
        photo_path = Path(contact_data['photo_path'])
        if photo_path.exists():
            dest = images_dir / f"{safe_name}{photo_path.suffix}"
            shutil.copy2(photo_path, dest)
            image_link = f"![Photo](images/{safe_name}{photo_path.suffix})"

    last_msg_date = contact_data.get('last_message_date', '')[:10] if contact_data.get('last_message_date') else 'unknown'
    first_msg_date = contact_data.get('first_message_date', '')[:10] if contact_data.get('first_message_date') else 'unknown'

    # Recent messages for context
    recent_context = ""
    for msg in contact_data.get('recent_messages', [])[:3]:
        recent_context += f"- {msg.get('date', '')[:10]}: {msg.get('from', 'Unknown')}: {msg.get('text', '')[:60]}...\n"

    content = f"""---
type: contact
entity_type: person
name: "{name}"
status: draft
relationship_status: {contact_data.get('status', 'dormant')}
relationship_score: {contact_data.get('relationship_score', 0)}
organization: "{company_link}"
industries: [{industries_yaml}]
channels:
  telegram: "{name}"
  phone: "{contact_data.get('phone', '')}"
source: telegram_export
telegram_stats:
  message_count: {contact_data.get('message_count', 0)}
  sent_count: {contact_data.get('sent_count', 0)}
  received_count: {contact_data.get('received_count', 0)}
  first_contact: {first_msg_date}
  last_contact: {last_msg_date}
  days_dormant: {contact_data.get('days_since_contact', 0)}
created: {datetime.now().strftime('%Y-%m-%d')}
---

# {name}

{image_link}

## Overview

Contact imported from Telegram. {contact_data.get('message_count', 0)} messages exchanged.

**Organization:** {company_link if company_link else 'Unknown'}
**Status:** {contact_data.get('status', 'Unknown').title()}
**Last Contact:** {last_msg_date}

## Notes

[Add notes about this contact]

## Goals

**What I want:**
- [Define goals with this contact]

**What they want:**
- [What can you offer them?]

## Recent Context

{recent_context if recent_context else 'No recent messages.'}

## Why Relevant for Organization

[Add relevance notes]

## Related

- [[Telegram Export December 2025]]
{f'- {company_link}' if company_link else ''}

#telegram, #crm, #{', #'.join(industries)}
"""

    filepath.write_text(content)
    return filepath, 'created'


def create_company_contact(company_name, people, output_dir):
    """Create a company contact stub (to be enriched with research)."""
    safe_name = safe_filename(company_name)
    filepath = output_dir / f"{safe_name}.md"

    if filepath.exists():
        return None, 'exists'

    # Aggregate info from people
    total_messages = sum(p.get('message_count', 0) for p in people)
    industries = list(set(ind for p in people for ind in p.get('industries', [])))

    people_list = "\n".join([f"| [[{p['name']}]] | {p.get('role', 'Unknown')} | {p.get('status', 'unknown')} |"
                             for p in people[:10]])

    content = f"""---
type: contact
entity_type: company
name: "{company_name}"
status: draft
relationship_status: discovered
industries: [{', '.join(industries)}]
key_people_count: {len(people)}
total_messages: {total_messages}
source: telegram_export
needs_research: true
created: {datetime.now().strftime('%Y-%m-%d')}
---

# {company_name}

## Overview

Company discovered from Telegram contacts. {len(people)} known contacts, {total_messages} total messages.

**Status:** Needs research
**Industries:** {', '.join(industries) if industries else 'Unknown'}

## Key Contacts

| Name | Role | Status |
|------|------|--------|
{people_list}

## Company Research

<!-- TO BE FILLED BY RESEARCH -->

**Website:**
**Description:**
**Stage:**
**Founded:**
**Headquarters:**

## Why Relevant for Organization

<!-- TO BE FILLED -->

- **Potential relationship:**
- **Strategic alignment:**
- **Technology overlap:**

## Industry Landscape Position

<!-- CONNECT TO LANDSCAPE -->

**Market position:**
**Competitors:**
**Partners:**

## Notes

[Add company notes]

## Related

{chr(10).join([f'- [[{p["name"]}]]' for p in people[:5]])}

#company, #needs-research, #{', #'.join(industries) if industries else 'crypto'}
"""

    filepath.write_text(content)
    return filepath, 'created'


def main():
    if len(sys.argv) < 2:
        print("Usage: python telegram_full_crm_processor.py /path/to/result.json")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    telegram_export_dir = json_path.parent

    print(f"Loading Telegram export from {json_path}...")
    with open(json_path, 'r') as f:
        data = json.load(f)

    now = datetime.now()

    # Ensure directories exist
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    COMPANIES_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Process chats
    chats = data.get('chats', {}).get('list', [])
    contacts_data = []
    companies_data = defaultdict(list)

    print(f"Processing {len(chats)} chats...")

    for i, chat in enumerate(chats):
        if chat.get('type') != 'personal_chat':
            continue

        name = chat.get('name', '')
        if not name or name == 'None':
            continue

        messages = chat.get('messages', [])
        if not messages:
            continue

        # Extract company from name
        company, clean_name = extract_company_from_name(name)

        # Calculate metrics
        first_msg_date = parse_date(messages[0].get('date')) if messages else None
        last_msg_date = parse_date(messages[-1].get('date')) if messages else None

        sent_count = sum(1 for m in messages if m.get('from', '').lower() == OWNER_NAME)
        received_count = len(messages) - sent_count

        days_since = (now - last_msg_date).days if last_msg_date else 999
        score = calculate_relationship_score(len(messages), days_since, sent_count, received_count)
        status = get_status_from_days(days_since)

        # Get recent messages
        recent_msgs = []
        for msg in messages[-5:]:
            text = msg.get('text', '')
            if isinstance(text, list):
                text = ' '.join(str(t.get('text', t) if isinstance(t, dict) else t) for t in text)
            recent_msgs.append({
                'date': msg.get('date', ''),
                'from': msg.get('from', 'Unknown'),
                'text': str(text)[:100] if text else ''
            })

        # Find photos (first available from chat)
        chat_folder = f"chat_{str(i+1).zfill(4)}"  # May not match exactly, search
        photos = []
        for folder in telegram_export_dir.glob('chats/chat_*'):
            # Check if this chat matches by looking for the name in messages
            photos_dir = folder / 'photos'
            if photos_dir.exists():
                for photo in photos_dir.iterdir():
                    if photo.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                        photos.append(photo)
                        break
            if photos:
                break

        industries = guess_industries(name, company)

        contact_data = {
            'name': name,
            'clean_name': clean_name,
            'company': company,
            'message_count': len(messages),
            'sent_count': sent_count,
            'received_count': received_count,
            'first_message_date': first_msg_date.isoformat() if first_msg_date else None,
            'last_message_date': last_msg_date.isoformat() if last_msg_date else None,
            'days_since_contact': days_since,
            'relationship_score': score,
            'status': status,
            'industries': industries,
            'recent_messages': recent_msgs,
            'photo_path': str(photos[0]) if photos else None,
            'phone': '',  # From contacts if available
        }

        contacts_data.append(contact_data)

        # Group by company
        if company:
            companies_data[company].append(contact_data)

    print(f"\nFound {len(contacts_data)} personal contacts")
    print(f"Extracted {len(companies_data)} companies")

    # Create person contacts
    print("\nCreating person contacts...")
    people_created = 0
    people_skipped = 0

    for contact in contacts_data:
        result, status = create_person_contact(contact, PEOPLE_DIR, IMAGES_DIR)
        if status == 'created':
            people_created += 1
        else:
            people_skipped += 1

        if (people_created + people_skipped) % 100 == 0:
            print(f"  Processed {people_created + people_skipped} contacts...")

    print(f"  Created: {people_created}, Skipped (existing): {people_skipped}")

    # Create company contacts
    print("\nCreating company contacts...")
    companies_created = 0
    companies_skipped = 0
    companies_for_research = []

    for company_name, people in companies_data.items():
        result, status = create_company_contact(company_name, people, COMPANIES_DIR)
        if status == 'created':
            companies_created += 1
            companies_for_research.append({
                'name': company_name,
                'people_count': len(people),
                'total_messages': sum(p['message_count'] for p in people),
                'industries': list(set(ind for p in people for ind in p.get('industries', [])))
            })
        else:
            companies_skipped += 1

    print(f"  Created: {companies_created}, Skipped (existing): {companies_skipped}")

    # Save manifest for research
    manifest_path = CONTACTS_DIR / 'companies_need_research.json'
    with open(manifest_path, 'w') as f:
        json.dump({
            'generated': now.isoformat(),
            'companies': sorted(companies_for_research,
                              key=lambda x: x['total_messages'],
                              reverse=True)
        }, f, indent=2)

    print(f"\nCompanies manifest saved to: {manifest_path}")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Person contacts created: {people_created}")
    print(f"Company contacts created: {companies_created}")
    print(f"Companies needing research: {len(companies_for_research)}")
    print(f"\nOutputs:")
    print(f"  - People: {PEOPLE_DIR}")
    print(f"  - Companies: {COMPANIES_DIR}")
    print(f"  - Images: {IMAGES_DIR}")
    print(f"  - Research manifest: {manifest_path}")


if __name__ == '__main__':
    main()
