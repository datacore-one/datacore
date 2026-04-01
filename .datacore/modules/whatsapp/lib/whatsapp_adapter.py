#!/usr/bin/env python3
"""
WhatsApp CRM Adapter

Implements CRMAdapter interface for WhatsApp interactions.
Extracts contact interactions from:
1. Parsed .txt exports
2. WAHA gateway (if connected)

Usage:
    adapter = WhatsAppAdapter(data_root)
    interactions = adapter.extract_interactions(since, until)
"""

import re
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import sys

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'lib'))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'crm' / 'lib'))

from whatsapp_export_parser import WhatsAppExportParser, ChatExport, parse_export_directory

# Try to import base CRMAdapter
try:
    from adapters import CRMAdapter, Interaction
except ImportError:
    # Fallback definitions if CRM module not available
    from abc import ABC, abstractmethod

    @dataclass
    class Interaction:
        contact: str
        date: str
        channel: str
        interaction_type: str
        summary: str
        source: str
        context: str = ""
        metadata: Dict[str, Any] = field(default_factory=dict)

    class CRMAdapter(ABC):
        @property
        @abstractmethod
        def adapter_type(self) -> str:
            pass

        @abstractmethod
        def extract_interactions(self, since: datetime, until: datetime = None) -> List[Interaction]:
            pass

        def resolve_contact(self, identifier: str) -> Optional[str]:
            return identifier


class WhatsAppAdapter(CRMAdapter):
    """Extract CRM interactions from WhatsApp exports and WAHA gateway."""

    def __init__(self, data_root: Path = None, waha_url: str = None):
        """Initialize WhatsApp adapter.

        Args:
            data_root: Path to ~/Data
            waha_url: Optional WAHA gateway URL for live data
        """
        self.data_root = Path(data_root) if data_root else Path.home() / 'Data'
        self.waha_url = waha_url

        # Paths
        self.exports_dir = self.data_root / '.datacore' / 'state' / 'whatsapp' / 'exports'
        self.processed_dir = self.data_root / '.datacore' / 'state' / 'whatsapp' / 'processed'
        self.phone_index_path = self.data_root / '.datacore' / 'state' / 'whatsapp' / 'phone-index.yaml'

        # Ensure directories exist
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        # Load phone index
        self.phone_index = self._load_phone_index()

        # Parser instance
        self.parser = WhatsAppExportParser()

    @property
    def adapter_type(self) -> str:
        return "whatsapp"

    def extract_interactions(self, since: datetime, until: datetime = None) -> List[Interaction]:
        """Extract WhatsApp interactions in date range.

        Args:
            since: Start of date range
            until: End of date range (default: now)

        Returns:
            List of Interaction objects
        """
        if until is None:
            until = datetime.now()

        interactions = []

        # 1. Extract from exports
        export_interactions = self._extract_from_exports(since, until)
        interactions.extend(export_interactions)

        # 2. Extract from WAHA (if available)
        if self.waha_url:
            waha_interactions = self._extract_from_waha(since, until)
            interactions.extend(waha_interactions)

        return interactions

    def _extract_from_exports(self, since: datetime, until: datetime) -> List[Interaction]:
        """Extract interactions from .txt export files."""
        interactions = []

        if not self.exports_dir.exists():
            return interactions

        # Parse all exports
        exports = parse_export_directory(self.exports_dir)

        for export in exports:
            # Filter messages in date range
            messages = export.messages_in_range(since, until)

            # Group by date for summarization
            by_date = {}
            for msg in messages:
                if msg.is_system:
                    continue

                date_str = msg.date_str
                if date_str not in by_date:
                    by_date[date_str] = []
                by_date[date_str].append(msg)

            # Create one interaction per contact per day
            for date_str, day_messages in by_date.items():
                # Group by sender
                by_sender = {}
                for msg in day_messages:
                    if msg.sender not in by_sender:
                        by_sender[msg.sender] = []
                    by_sender[msg.sender].append(msg)

                for sender, sender_messages in by_sender.items():
                    # Resolve contact name
                    contact_name = self.resolve_contact(sender)

                    # Generate summary
                    summary = self._generate_summary(sender_messages)

                    # Determine interaction type
                    interaction_type = self._determine_interaction_type(sender_messages)

                    interactions.append(Interaction(
                        contact=contact_name,
                        date=date_str,
                        channel='whatsapp',
                        interaction_type=interaction_type,
                        summary=summary,
                        source=f"whatsapp:{export.chat_name}",
                        context=self._extract_context(sender_messages),
                        metadata={
                            'message_count': len(sender_messages),
                            'chat_name': export.chat_name,
                            'chat_type': export.chat_type,
                        }
                    ))

        return interactions

    def _extract_from_waha(self, since: datetime, until: datetime) -> List[Interaction]:
        """Extract interactions from WAHA gateway (if connected)."""
        # TODO: Implement WAHA API integration
        # This would query the WAHA gateway for recent messages
        return []

    def resolve_contact(self, identifier: str) -> Optional[str]:
        """Resolve phone number or WhatsApp name to contact.

        Args:
            identifier: Phone number or display name from WhatsApp

        Returns:
            Resolved contact name or original identifier
        """
        # Check phone index first
        if identifier in self.phone_index:
            return self.phone_index[identifier]

        # Try to find in contacts by phone number
        contacts_dir = self.data_root / '0-personal' / 'contacts' / 'people'
        if contacts_dir.exists():
            for contact_file in contacts_dir.glob('*.md'):
                contact_name = self._check_contact_phone(contact_file, identifier)
                if contact_name:
                    # Cache in index
                    self.phone_index[identifier] = contact_name
                    self._save_phone_index()
                    return contact_name

        # Return original identifier
        return identifier

    def _check_contact_phone(self, contact_file: Path, identifier: str) -> Optional[str]:
        """Check if contact file has matching phone number."""
        try:
            with open(contact_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Look for frontmatter
            if content.startswith('---'):
                end = content.find('---', 3)
                if end > 0:
                    frontmatter = content[3:end]

                    # Check for phone in channels.whatsapp
                    if 'whatsapp:' in frontmatter:
                        # Normalize phone numbers for comparison
                        identifier_normalized = self._normalize_phone(identifier)

                        for line in frontmatter.split('\n'):
                            if 'whatsapp:' in line or 'phone:' in line or 'mobile:' in line:
                                phone_match = re.search(r'["\']?([+\d\s\-()]+)["\']?', line)
                                if phone_match:
                                    file_phone = self._normalize_phone(phone_match.group(1))
                                    if file_phone == identifier_normalized:
                                        # Extract name from filename
                                        name = contact_file.stem.split(' | ')[0]
                                        return name

        except Exception:
            pass

        return None

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number for comparison."""
        # Remove all non-digits except +
        normalized = re.sub(r'[^\d+]', '', phone)
        # Remove leading + if present
        if normalized.startswith('+'):
            normalized = normalized[1:]
        return normalized

    def _generate_summary(self, messages: list) -> str:
        """Generate summary from messages."""
        if not messages:
            return ""

        msg_count = len(messages)

        # Get first non-trivial message for context
        sample = ""
        for msg in messages:
            if len(msg.content) > 10 and msg.media_type is None:
                sample = msg.content[:100]
                break

        if sample:
            return f"{msg_count} messages. Sample: \"{sample}...\""
        else:
            return f"{msg_count} messages exchanged"

    def _determine_interaction_type(self, messages: list) -> str:
        """Determine interaction type from message content."""
        has_media = any(m.media_type for m in messages)
        msg_count = len(messages)

        if has_media:
            return 'message'  # Media sharing
        elif msg_count > 10:
            return 'conversation'  # Extended chat
        else:
            return 'message'

    def _extract_context(self, messages: list, max_messages: int = 3) -> str:
        """Extract context from recent messages."""
        context_parts = []

        for msg in messages[:max_messages]:
            content = msg.content[:100]
            context_parts.append(f"[{msg.time_str}] {content}")

        return '\n'.join(context_parts)

    def _load_phone_index(self) -> Dict[str, str]:
        """Load phone number to contact name mapping."""
        if self.phone_index_path.exists():
            try:
                with open(self.phone_index_path, 'r') as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

    def _save_phone_index(self):
        """Save phone index to disk."""
        try:
            with open(self.phone_index_path, 'w') as f:
                yaml.dump(self.phone_index, f, default_flow_style=False)
        except Exception:
            pass

    def get_export_stats(self) -> Dict[str, Any]:
        """Get statistics about available exports."""
        if not self.exports_dir.exists():
            return {'export_count': 0, 'exports': []}

        exports = parse_export_directory(self.exports_dir)

        stats = {
            'export_count': len(exports),
            'total_messages': sum(e.message_count for e in exports),
            'total_participants': len(set(
                p for e in exports for p in e.participants
            )),
            'exports': [
                {
                    'chat_name': e.chat_name,
                    'chat_type': e.chat_type,
                    'messages': e.message_count,
                    'participants': e.participants,
                    'date_range': (
                        e.date_range[0].isoformat() if e.date_range else None,
                        e.date_range[1].isoformat() if e.date_range else None,
                    )
                }
                for e in exports
            ]
        }

        return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WhatsApp CRM Adapter")
    parser.add_argument('--scan', action='store_true', help='Scan exports for interactions')
    parser.add_argument('--days', type=int, default=30, help='Days to scan')
    parser.add_argument('--stats', action='store_true', help='Show export statistics')

    args = parser.parse_args()

    adapter = WhatsAppAdapter()

    if args.stats:
        stats = adapter.get_export_stats()
        print(f"\n=== WhatsApp Export Statistics ===")
        print(f"Exports: {stats['export_count']}")
        print(f"Total messages: {stats['total_messages']}")
        print(f"Total participants: {stats['total_participants']}")

        for export in stats['exports']:
            print(f"\n  {export['chat_name']} ({export['chat_type']})")
            print(f"    Messages: {export['messages']}")
            print(f"    Participants: {', '.join(export['participants'][:5])}")

    elif args.scan:
        since = datetime.now() - timedelta(days=args.days)
        interactions = adapter.extract_interactions(since)

        print(f"\n=== WhatsApp Interactions (last {args.days} days) ===")
        print(f"Found {len(interactions)} interactions\n")

        for i in interactions[:10]:
            print(f"  {i.date} | {i.contact} | {i.interaction_type}")
            print(f"    {i.summary[:60]}")
            print()
    else:
        parser.print_help()
