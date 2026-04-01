#!/usr/bin/env python3
"""
WhatsApp Contact Creator

Creates CRM contact files from WhatsApp exports.
Handles duplicate detection, matching with existing contacts,
and proper formatting according to CRM module patterns.

Usage:
    creator = WhatsAppContactCreator(data_root)
    created = creator.create_contacts_from_export(export_path, space='0-personal')
"""

import re
import yaml
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass

from whatsapp_export_parser import WhatsAppExportParser, ChatExport, parse_export_directory


@dataclass
class ContactCandidate:
    """A potential contact extracted from WhatsApp."""
    name: str
    phone: Optional[str] = None
    message_count: int = 0
    first_contact: Optional[datetime] = None
    last_contact: Optional[datetime] = None
    chat_names: List[str] = None
    is_group: bool = False

    def __post_init__(self):
        if self.chat_names is None:
            self.chat_names = []

    @property
    def days_active(self) -> int:
        if self.first_contact and self.last_contact:
            return (self.last_contact - self.first_contact).days
        return 0


class WhatsAppContactCreator:
    """Create and manage CRM contacts from WhatsApp data."""

    CONTACT_TEMPLATE = '''---
type: contact
entity_type: person
name: "{name}"
status: draft
relationship_status: new
relevance: {relevance}
privacy: personal
channels:
  whatsapp: "{whatsapp_id}"
source: whatsapp_export
whatsapp_stats:
  message_count: {message_count}
  first_contact: {first_contact}
  last_contact: {last_contact}
  days_active: {days_active}
  chats: {chats}
created: {created}
---

# {name}

## Overview

Contact imported from WhatsApp.

**Messages:** {message_count} across {chat_count} chat(s)
**First contact:** {first_contact}
**Last contact:** {last_contact}
**Days active:** {days_active}

## WhatsApp Chats

{chat_list}

## Notes

[Add context: How do you know this person? What's their background?]

**Key Topics:**
- [Main discussion topics from WhatsApp]

## Goals

**What I want:**
- [Your objectives with this contact]

**What they want:**
- [What can you offer them?]

## Follow-up

- [ ] Review WhatsApp history
- [ ] Add more context
- [ ] Connect on LinkedIn

## Related

- [[WhatsApp Import {import_date}]]
'''

    def __init__(self, data_root: Path = None):
        """Initialize contact creator.

        Args:
            data_root: Path to ~/Data
        """
        self.data_root = Path(data_root) if data_root else Path.home() / 'Data'
        self.exports_dir = self.data_root / '.datacore' / 'state' / 'whatsapp' / 'exports'
        self.parser = WhatsAppExportParser()

    def create_contacts_from_exports(self, space: str = '0-personal',
                                      dry_run: bool = False) -> Dict[str, Any]:
        """Create contacts from all exports in the exports directory.

        Args:
            space: Target space for contacts
            dry_run: If True, don't actually create files

        Returns:
            Dict with creation results
        """
        if not self.exports_dir.exists():
            return {'error': 'Exports directory does not exist', 'created': [], 'skipped': []}

        # Parse all exports
        exports = parse_export_directory(self.exports_dir)

        if not exports:
            return {'error': 'No exports found', 'created': [], 'skipped': []}

        # Extract all contact candidates
        candidates = self._extract_candidates(exports)

        # Create contacts
        return self._create_contacts(candidates, space, dry_run)

    def create_contacts_from_export(self, export_path: Path, space: str = '0-personal',
                                     dry_run: bool = False) -> Dict[str, Any]:
        """Create contacts from a single export file.

        Args:
            export_path: Path to .txt export file
            space: Target space for contacts
            dry_run: If True, don't actually create files

        Returns:
            Dict with creation results
        """
        export = self.parser.parse_file(export_path)
        candidates = self._extract_candidates([export])
        return self._create_contacts(candidates, space, dry_run)

    def _extract_candidates(self, exports: List[ChatExport]) -> Dict[str, ContactCandidate]:
        """Extract contact candidates from exports.

        Args:
            exports: List of parsed exports

        Returns:
            Dict mapping identifier to ContactCandidate
        """
        candidates = {}

        for export in exports:
            # Skip if this is a group with too many participants
            is_group = export.chat_type == 'group'

            for participant in export.participants:
                # Skip "You" entries
                if self._is_self(participant):
                    continue

                # Get or create candidate
                key = self._normalize_identifier(participant)

                if key not in candidates:
                    candidates[key] = ContactCandidate(
                        name=participant,
                        message_count=0,
                        chat_names=[],
                        is_group=is_group
                    )

                candidate = candidates[key]

                # Update stats
                participant_messages = [m for m in export.messages if m.sender == participant]
                candidate.message_count += len(participant_messages)

                if export.chat_name not in candidate.chat_names:
                    candidate.chat_names.append(export.chat_name)

                # Update date range
                if participant_messages:
                    dates = [m.timestamp for m in participant_messages]
                    min_date, max_date = min(dates), max(dates)

                    if candidate.first_contact is None or min_date < candidate.first_contact:
                        candidate.first_contact = min_date
                    if candidate.last_contact is None or max_date > candidate.last_contact:
                        candidate.last_contact = max_date

        return candidates

    def _is_self(self, name: str) -> bool:
        """Check if this is the user's own name."""
        self_indicators = ['you', 'me']  # Add your name variations
        return name.lower() in self_indicators

    def _normalize_identifier(self, identifier: str) -> str:
        """Normalize identifier for deduplication."""
        # Remove phone number formatting
        normalized = re.sub(r'[^\w\s]', '', identifier)
        normalized = normalized.lower().strip()
        return normalized

    def _create_contacts(self, candidates: Dict[str, ContactCandidate],
                         space: str, dry_run: bool) -> Dict[str, Any]:
        """Create contact files for candidates.

        Args:
            candidates: Dict of contact candidates
            space: Target space
            dry_run: If True, don't create files

        Returns:
            Results dict
        """
        contacts_dir = self.data_root / space / 'contacts' / 'people'
        contacts_dir.mkdir(parents=True, exist_ok=True)

        created = []
        skipped = []
        matched = []

        for key, candidate in candidates.items():
            # Skip low-activity contacts
            if candidate.message_count < 3:
                skipped.append({
                    'name': candidate.name,
                    'reason': 'low_activity',
                    'message_count': candidate.message_count
                })
                continue

            # Check for existing contact
            existing = self._find_existing_contact(candidate, contacts_dir)
            if existing:
                matched.append({
                    'name': candidate.name,
                    'existing': existing.name,
                    'action': 'update_stats'
                })
                if not dry_run:
                    self._update_existing_contact(existing, candidate)
                continue

            # Create new contact
            if not dry_run:
                contact_path = self._create_contact_file(candidate, contacts_dir)
                created.append({
                    'name': candidate.name,
                    'path': str(contact_path),
                    'message_count': candidate.message_count
                })
            else:
                created.append({
                    'name': candidate.name,
                    'path': '[dry run]',
                    'message_count': candidate.message_count
                })

        return {
            'created': created,
            'skipped': skipped,
            'matched': matched,
            'total_candidates': len(candidates)
        }

    def _find_existing_contact(self, candidate: ContactCandidate,
                               contacts_dir: Path) -> Optional[Path]:
        """Find existing contact that matches candidate.

        Args:
            candidate: Contact candidate
            contacts_dir: Contacts directory

        Returns:
            Path to existing contact or None
        """
        # Normalize candidate name for matching
        candidate_normalized = self._normalize_identifier(candidate.name)
        candidate_words = set(candidate_normalized.split())

        for contact_file in contacts_dir.glob('*.md'):
            # Check filename match
            filename_normalized = self._normalize_identifier(contact_file.stem.split(' | ')[0])

            # Exact match
            if filename_normalized == candidate_normalized:
                return contact_file

            # Word overlap match (for partial names)
            filename_words = set(filename_normalized.split())
            overlap = candidate_words & filename_words
            if len(overlap) >= 2 or (len(overlap) == 1 and len(candidate_words) == 1):
                return contact_file

            # Check frontmatter for WhatsApp ID match
            try:
                with open(contact_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                if candidate.name in content or candidate.phone and candidate.phone in content:
                    return contact_file
            except Exception:
                pass

        return None

    def _update_existing_contact(self, contact_path: Path, candidate: ContactCandidate):
        """Update existing contact with WhatsApp stats."""
        try:
            with open(contact_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Add WhatsApp channel if not present
            if 'whatsapp:' not in content.lower():
                # Find channels section and add
                if 'channels:' in content:
                    content = re.sub(
                        r'(channels:\n)',
                        f'\\1  whatsapp: "{candidate.name}"\n',
                        content
                    )

            # Add WhatsApp stats section if not present
            if 'whatsapp_stats:' not in content:
                stats_section = f'''whatsapp_stats:
  message_count: {candidate.message_count}
  first_contact: {candidate.first_contact.strftime('%Y-%m-%d') if candidate.first_contact else 'unknown'}
  last_contact: {candidate.last_contact.strftime('%Y-%m-%d') if candidate.last_contact else 'unknown'}
  chats: {candidate.chat_names}
'''
                # Insert before ---
                if content.startswith('---'):
                    end_frontmatter = content.find('---', 3)
                    if end_frontmatter > 0:
                        content = content[:end_frontmatter] + stats_section + content[end_frontmatter:]

            with open(contact_path, 'w', encoding='utf-8') as f:
                f.write(content)

        except Exception as e:
            print(f"Error updating {contact_path}: {e}")

    def _create_contact_file(self, candidate: ContactCandidate, contacts_dir: Path) -> Path:
        """Create a new contact file.

        Args:
            candidate: Contact candidate
            contacts_dir: Target directory

        Returns:
            Path to created file
        """
        # Determine relevance based on activity
        if candidate.message_count > 100:
            relevance = 4
        elif candidate.message_count > 30:
            relevance = 3
        elif candidate.message_count > 10:
            relevance = 2
        else:
            relevance = 1

        # Format dates
        first_contact = candidate.first_contact.strftime('%Y-%m-%d') if candidate.first_contact else 'unknown'
        last_contact = candidate.last_contact.strftime('%Y-%m-%d') if candidate.last_contact else 'unknown'

        # Format chat list
        chat_list = '\n'.join([f'- {chat}' for chat in candidate.chat_names])

        # Generate content
        content = self.CONTACT_TEMPLATE.format(
            name=candidate.name,
            relevance=relevance,
            whatsapp_id=candidate.name,
            message_count=candidate.message_count,
            first_contact=first_contact,
            last_contact=last_contact,
            days_active=candidate.days_active,
            chats=candidate.chat_names,
            created=datetime.now().strftime('%Y-%m-%d'),
            chat_count=len(candidate.chat_names),
            chat_list=chat_list,
            import_date=datetime.now().strftime('%Y-%m-%d'),
        )

        # Create filename
        filename = self._sanitize_filename(candidate.name) + '.md'
        contact_path = contacts_dir / filename

        # Handle duplicates
        counter = 1
        while contact_path.exists():
            filename = f"{self._sanitize_filename(candidate.name)} ({counter}).md"
            contact_path = contacts_dir / filename
            counter += 1

        with open(contact_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return contact_path

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize name for use as filename."""
        # Remove/replace invalid characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '', name)
        sanitized = sanitized.strip()
        # Limit length
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized

    def get_import_preview(self) -> Dict[str, Any]:
        """Get preview of what would be imported.

        Returns:
            Preview dict with candidates
        """
        if not self.exports_dir.exists():
            return {'error': 'Exports directory does not exist', 'candidates': []}

        exports = parse_export_directory(self.exports_dir)
        candidates = self._extract_candidates(exports)

        preview = {
            'export_count': len(exports),
            'candidate_count': len(candidates),
            'candidates': [
                {
                    'name': c.name,
                    'message_count': c.message_count,
                    'chats': c.chat_names,
                    'first_contact': c.first_contact.strftime('%Y-%m-%d') if c.first_contact else None,
                    'last_contact': c.last_contact.strftime('%Y-%m-%d') if c.last_contact else None,
                }
                for c in sorted(candidates.values(), key=lambda x: -x.message_count)
            ]
        }

        return preview


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WhatsApp Contact Creator")
    parser.add_argument('--preview', action='store_true', help='Preview contacts to be created')
    parser.add_argument('--create', action='store_true', help='Create contacts from exports')
    parser.add_argument('--space', default='0-personal', help='Target space')
    parser.add_argument('--dry-run', action='store_true', help='Dry run (no file creation)')

    args = parser.parse_args()

    creator = WhatsAppContactCreator()

    if args.preview:
        preview = creator.get_import_preview()

        print(f"\n=== WhatsApp Import Preview ===")
        print(f"Exports: {preview.get('export_count', 0)}")
        print(f"Candidates: {preview.get('candidate_count', 0)}")

        print(f"\nTop contacts to import:")
        for c in preview.get('candidates', [])[:20]:
            print(f"  {c['name']}: {c['message_count']} messages")
            print(f"    Chats: {', '.join(c['chats'][:3])}")

    elif args.create:
        results = creator.create_contacts_from_exports(
            space=args.space,
            dry_run=args.dry_run
        )

        print(f"\n=== WhatsApp Contact Creation ===")
        print(f"Created: {len(results['created'])}")
        print(f"Matched existing: {len(results['matched'])}")
        print(f"Skipped: {len(results['skipped'])}")

        if results['created']:
            print(f"\nCreated contacts:")
            for c in results['created']:
                print(f"  + {c['name']} ({c['message_count']} messages)")

    else:
        parser.print_help()
