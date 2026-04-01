#!/usr/bin/env python3
"""
WhatsApp Export Parser

Parses WhatsApp chat export .txt files (iOS and Android formats).
Extracts messages, participants, timestamps, and metadata.

Usage:
    parser = WhatsAppExportParser()
    export = parser.parse_file('/path/to/chat.txt')
    print(f"Chat: {export.chat_name}, {len(export.messages)} messages")
"""

import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum


class ExportFormat(Enum):
    """WhatsApp export format variants."""
    IOS = "ios"
    ANDROID = "android"
    UNKNOWN = "unknown"


@dataclass
class Message:
    """A single WhatsApp message."""
    timestamp: datetime
    sender: str
    content: str
    is_system: bool = False  # System messages (joined, left, etc.)
    media_type: Optional[str] = None  # image, video, audio, document
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def date_str(self) -> str:
        return self.timestamp.strftime('%Y-%m-%d')

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime('%H:%M')


@dataclass
class ChatExport:
    """Parsed WhatsApp chat export."""
    chat_name: str
    chat_type: str  # 'individual' or 'group'
    participants: List[str]
    messages: List[Message]
    export_format: ExportFormat
    source_file: str
    date_range: Tuple[datetime, datetime] = None

    def __post_init__(self):
        if self.messages and not self.date_range:
            dates = [m.timestamp for m in self.messages if m.timestamp]
            if dates:
                self.date_range = (min(dates), max(dates))

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    def messages_by_sender(self) -> Dict[str, List[Message]]:
        """Group messages by sender."""
        by_sender = {}
        for msg in self.messages:
            if msg.sender not in by_sender:
                by_sender[msg.sender] = []
            by_sender[msg.sender].append(msg)
        return by_sender

    def messages_in_range(self, since: datetime, until: datetime = None) -> List[Message]:
        """Filter messages by date range."""
        if until is None:
            until = datetime.now()
        return [m for m in self.messages if since <= m.timestamp <= until]


class WhatsAppExportParser:
    """Parse WhatsApp .txt chat exports."""

    # iOS format: [DD/MM/YYYY, HH:MM:SS] Name: Message
    # Also handles: [DD.MM.YYYY, HH:MM:SS] and [MM/DD/YYYY, HH:MM:SS]
    IOS_PATTERNS = [
        # [DD/MM/YYYY, HH:MM:SS] Name: Message
        re.compile(r'^\[(\d{1,2}/\d{1,2}/\d{4}), (\d{1,2}:\d{2}:\d{2})\] ([^:]+): (.*)$'),
        # [DD.MM.YYYY, HH:MM:SS] Name: Message (European)
        re.compile(r'^\[(\d{1,2}\.\d{1,2}\.\d{4}), (\d{1,2}:\d{2}:\d{2})\] ([^:]+): (.*)$'),
    ]

    # Android format: DD/MM/YYYY, HH:MM - Name: Message
    ANDROID_PATTERNS = [
        # DD/MM/YYYY, HH:MM - Name: Message
        re.compile(r'^(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}) - ([^:]+): (.*)$'),
        # DD.MM.YYYY, HH:MM - Name: Message (European)
        re.compile(r'^(\d{1,2}\.\d{1,2}\.\d{2,4}), (\d{1,2}:\d{2}) - ([^:]+): (.*)$'),
        # MM/DD/YY, HH:MM - Name: Message (US)
        re.compile(r'^(\d{1,2}/\d{1,2}/\d{2}), (\d{1,2}:\d{2}) - ([^:]+): (.*)$'),
    ]

    # System message patterns (no sender)
    SYSTEM_PATTERNS = [
        re.compile(r'^\[(\d{1,2}[/\.]\d{1,2}[/\.]\d{2,4}), (\d{1,2}:\d{2}(?::\d{2})?)\] (.+)$'),
        re.compile(r'^(\d{1,2}[/\.]\d{1,2}[/\.]\d{2,4}), (\d{1,2}:\d{2}) - (.+)$'),
    ]

    # Media placeholders
    MEDIA_PATTERNS = {
        'image': re.compile(r'<Media omitted>|image omitted|‎image omitted', re.IGNORECASE),
        'video': re.compile(r'video omitted|‎video omitted', re.IGNORECASE),
        'audio': re.compile(r'audio omitted|‎audio omitted', re.IGNORECASE),
        'document': re.compile(r'document omitted|‎document omitted', re.IGNORECASE),
        'sticker': re.compile(r'sticker omitted|‎sticker omitted', re.IGNORECASE),
        'gif': re.compile(r'GIF omitted|‎GIF omitted', re.IGNORECASE),
    }

    # System message indicators
    SYSTEM_INDICATORS = [
        'created group',
        'added you',
        'removed you',
        'left',
        'joined',
        'changed the subject',
        'changed this group',
        'changed the group',
        'Messages and calls are end-to-end encrypted',
        'security code changed',
        'deleted this message',
        'This message was deleted',
    ]

    def __init__(self):
        self.detected_format: ExportFormat = ExportFormat.UNKNOWN

    def parse_file(self, file_path: Path) -> ChatExport:
        """Parse a WhatsApp export file.

        Args:
            file_path: Path to .txt export file

        Returns:
            ChatExport object with parsed data
        """
        file_path = Path(file_path)

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return self.parse_content(content, source_file=str(file_path))

    def parse_content(self, content: str, source_file: str = "unknown") -> ChatExport:
        """Parse WhatsApp export content string.

        Args:
            content: Raw export text content
            source_file: Source filename for reference

        Returns:
            ChatExport object with parsed data
        """
        # Detect format
        self.detected_format = self.detect_format(content)

        # Parse messages
        messages = self._parse_messages(content)

        # Extract participants (excluding system)
        participants = self._extract_participants(messages)

        # Determine chat name and type
        chat_name = self._infer_chat_name(source_file, participants)
        chat_type = 'group' if len(participants) > 2 else 'individual'

        return ChatExport(
            chat_name=chat_name,
            chat_type=chat_type,
            participants=participants,
            messages=messages,
            export_format=self.detected_format,
            source_file=source_file,
        )

    def detect_format(self, content: str) -> ExportFormat:
        """Detect whether export is iOS or Android format.

        Args:
            content: Raw export text

        Returns:
            ExportFormat enum value
        """
        lines = content.split('\n')[:20]  # Check first 20 lines

        ios_matches = 0
        android_matches = 0

        for line in lines:
            for pattern in self.IOS_PATTERNS:
                if pattern.match(line):
                    ios_matches += 1
                    break

            for pattern in self.ANDROID_PATTERNS:
                if pattern.match(line):
                    android_matches += 1
                    break

        if ios_matches > android_matches:
            return ExportFormat.IOS
        elif android_matches > ios_matches:
            return ExportFormat.ANDROID
        else:
            return ExportFormat.UNKNOWN

    def _parse_messages(self, content: str) -> List[Message]:
        """Parse all messages from content."""
        messages = []
        lines = content.split('\n')

        current_message = None

        for line in lines:
            parsed = self._parse_line(line)

            if parsed:
                # New message starts
                if current_message:
                    messages.append(current_message)
                current_message = parsed
            elif current_message and line.strip():
                # Continuation of previous message (multi-line)
                current_message.content += '\n' + line

        # Don't forget last message
        if current_message:
            messages.append(current_message)

        return messages

    def _parse_line(self, line: str) -> Optional[Message]:
        """Parse a single line into a Message or None."""
        line = line.strip()
        if not line:
            return None

        # Try iOS patterns
        for pattern in self.IOS_PATTERNS:
            match = pattern.match(line)
            if match:
                date_str, time_str, sender, content = match.groups()
                timestamp = self._parse_timestamp(date_str, time_str, has_seconds=True)
                return self._create_message(timestamp, sender, content)

        # Try Android patterns
        for pattern in self.ANDROID_PATTERNS:
            match = pattern.match(line)
            if match:
                date_str, time_str, sender, content = match.groups()
                timestamp = self._parse_timestamp(date_str, time_str, has_seconds=False)
                return self._create_message(timestamp, sender, content)

        # Try system message patterns
        for pattern in self.SYSTEM_PATTERNS:
            match = pattern.match(line)
            if match:
                groups = match.groups()
                if len(groups) == 3:
                    date_str, time_str, content = groups
                    has_seconds = ':' in time_str and time_str.count(':') == 2
                    timestamp = self._parse_timestamp(date_str, time_str, has_seconds=has_seconds)
                    return Message(
                        timestamp=timestamp,
                        sender="System",
                        content=content,
                        is_system=True
                    )

        return None

    def _parse_timestamp(self, date_str: str, time_str: str, has_seconds: bool = False) -> datetime:
        """Parse date and time strings into datetime."""
        # Normalize separators
        date_str = date_str.replace('.', '/')

        # Try different date formats
        date_formats = [
            '%d/%m/%Y',  # DD/MM/YYYY
            '%m/%d/%Y',  # MM/DD/YYYY
            '%d/%m/%y',  # DD/MM/YY
            '%m/%d/%y',  # MM/DD/YY
        ]

        time_format = '%H:%M:%S' if has_seconds else '%H:%M'

        for date_fmt in date_formats:
            try:
                full_format = f"{date_fmt}, {time_format}"
                return datetime.strptime(f"{date_str}, {time_str}", full_format)
            except ValueError:
                continue

        # Fallback: try without comma
        for date_fmt in date_formats:
            try:
                full_format = f"{date_fmt} {time_format}"
                return datetime.strptime(f"{date_str} {time_str}", full_format)
            except ValueError:
                continue

        # Last resort: return epoch
        return datetime(1970, 1, 1)

    def _create_message(self, timestamp: datetime, sender: str, content: str) -> Message:
        """Create a Message object with proper metadata."""
        is_system = self._is_system_message(content, sender)
        media_type = self._detect_media_type(content)

        return Message(
            timestamp=timestamp,
            sender=sender.strip(),
            content=content.strip(),
            is_system=is_system,
            media_type=media_type,
        )

    def _is_system_message(self, content: str, sender: str) -> bool:
        """Check if message is a system notification."""
        content_lower = content.lower()

        for indicator in self.SYSTEM_INDICATORS:
            if indicator.lower() in content_lower:
                return True

        return False

    def _detect_media_type(self, content: str) -> Optional[str]:
        """Detect if message contains media placeholder."""
        for media_type, pattern in self.MEDIA_PATTERNS.items():
            if pattern.search(content):
                return media_type
        return None

    def _extract_participants(self, messages: List[Message]) -> List[str]:
        """Extract unique participants from messages."""
        participants = set()

        for msg in messages:
            if not msg.is_system and msg.sender != "System":
                participants.add(msg.sender)

        return sorted(list(participants))

    def _infer_chat_name(self, source_file: str, participants: List[str]) -> str:
        """Infer chat name from filename or participants."""
        # Try to extract from filename
        # Common patterns: "WhatsApp Chat with John.txt", "WhatsApp Chat - Group Name.txt"
        filename = Path(source_file).stem

        # Remove common prefixes
        prefixes = ['WhatsApp Chat with ', 'WhatsApp Chat - ', 'Chat with ']
        for prefix in prefixes:
            if filename.startswith(prefix):
                return filename[len(prefix):]

        # Use filename as-is if reasonable
        if filename and not filename.startswith('_'):
            return filename

        # Fallback to participants
        if len(participants) == 1:
            return participants[0]
        elif len(participants) == 2:
            return f"{participants[0]} & {participants[1]}"
        else:
            return f"Group ({len(participants)} participants)"


def parse_export_directory(directory: Path) -> List[ChatExport]:
    """Parse all .txt exports in a directory.

    Args:
        directory: Path to directory containing .txt exports

    Returns:
        List of ChatExport objects
    """
    parser = WhatsAppExportParser()
    exports = []

    for txt_file in Path(directory).glob('*.txt'):
        try:
            export = parser.parse_file(txt_file)
            exports.append(export)
        except Exception as e:
            print(f"Error parsing {txt_file}: {e}")

    return exports


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python whatsapp_export_parser.py <export.txt>")
        sys.exit(1)

    file_path = Path(sys.argv[1])

    if file_path.is_dir():
        exports = parse_export_directory(file_path)
        print(f"\nParsed {len(exports)} exports:")
        for export in exports:
            print(f"  - {export.chat_name}: {export.message_count} messages, "
                  f"{export.participant_count} participants")
    else:
        parser = WhatsAppExportParser()
        export = parser.parse_file(file_path)

        print(f"\n=== {export.chat_name} ===")
        print(f"Type: {export.chat_type}")
        print(f"Format: {export.export_format.value}")
        print(f"Messages: {export.message_count}")
        print(f"Participants: {', '.join(export.participants)}")

        if export.date_range:
            print(f"Date range: {export.date_range[0].date()} to {export.date_range[1].date()}")

        print(f"\nMessages by sender:")
        for sender, msgs in export.messages_by_sender().items():
            print(f"  {sender}: {len(msgs)} messages")

        print(f"\nFirst 5 messages:")
        for msg in export.messages[:5]:
            print(f"  [{msg.date_str} {msg.time_str}] {msg.sender}: {msg.content[:50]}...")
