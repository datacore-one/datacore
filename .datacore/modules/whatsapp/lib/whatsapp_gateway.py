#!/usr/bin/env python3
"""
WhatsApp Gateway

Bidirectional WhatsApp gateway for Datacore.
Handles incoming messages, command routing, and inbox capture.

Usage:
    gateway = WhatsAppGateway(waha_url="http://localhost:3000")
    await gateway.start()  # Start listening for messages
"""

import asyncio
import re
import yaml
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Awaitable
from enum import Enum

from waha_client import (
    WAHAClient,
    WAHAClientSync,
    WAHAMessage,
    WAHAWebhookHandler,
    SessionStatus,
    WAHAError,
)


class CommandType(Enum):
    """Types of commands the gateway can handle."""
    TODAY = "today"
    TOMORROW = "tomorrow"
    INBOX = "inbox"
    CRM_LOOKUP = "crm_lookup"
    SEARCH = "search"
    TASK = "task"
    REMINDER = "reminder"
    STATUS = "status"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    """Parsed command from WhatsApp message."""
    type: CommandType
    args: str = ""
    raw_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayConfig:
    """Gateway configuration."""
    waha_url: str = "http://localhost:3000"
    session_name: str = "default"
    allowed_numbers: List[str] = field(default_factory=list)
    owner_number: Optional[str] = None
    auto_capture: bool = True
    webhook_port: int = 8080
    data_root: Path = None

    def __post_init__(self):
        if self.data_root is None:
            self.data_root = Path.home() / "Data"

    @classmethod
    def from_yaml(cls, path: Path) -> "GatewayConfig":
        """Load config from YAML file."""
        if path.exists():
            with open(path, 'r') as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()


class WhatsAppGateway:
    """Bidirectional WhatsApp gateway for Datacore."""

    # Command patterns (case-insensitive)
    COMMAND_PATTERNS = {
        CommandType.TODAY: re.compile(r'^/?(today|briefing|morning)$', re.I),
        CommandType.TOMORROW: re.compile(r'^/?(tomorrow|evening|wrap-?up)$', re.I),
        CommandType.INBOX: re.compile(r'^/?(inbox|capture):?\s*(.*)$', re.I),
        CommandType.CRM_LOOKUP: re.compile(r'^/?(who\s+is|lookup|contact)\s+(.+)$', re.I),
        CommandType.SEARCH: re.compile(r'^/?(search|find|datacortex)\s+(.+)$', re.I),
        CommandType.TASK: re.compile(r'^/?(task|todo|add\s+task):?\s*(.+)$', re.I),
        CommandType.REMINDER: re.compile(r'^/?(remind|reminder)\s+(.+)$', re.I),
        CommandType.STATUS: re.compile(r'^/?(status|ping)$', re.I),
        CommandType.HELP: re.compile(r'^/?(help|\?)$', re.I),
    }

    def __init__(
        self,
        config: GatewayConfig = None,
        waha_url: str = None,
        allowed_numbers: List[str] = None,
        data_root: Path = None,
    ):
        """Initialize WhatsApp gateway.

        Args:
            config: Gateway configuration object
            waha_url: WAHA server URL (overrides config)
            allowed_numbers: Allowed phone numbers (overrides config)
            data_root: Path to ~/Data (overrides config)
        """
        self.config = config or GatewayConfig()

        if waha_url:
            self.config.waha_url = waha_url
        if allowed_numbers:
            self.config.allowed_numbers = allowed_numbers
        if data_root:
            self.config.data_root = Path(data_root)

        self.client = WAHAClient(
            base_url=self.config.waha_url,
            session_name=self.config.session_name,
        )

        self.webhook_handler = WAHAWebhookHandler()
        self.webhook_handler.on_message(self._handle_message)
        self.webhook_handler.on_status_change(self._handle_status_change)

        # Custom command handlers
        self._custom_handlers: Dict[CommandType, Callable] = {}

        # Message log
        self.log_path = self.config.data_root / '.datacore' / 'state' / 'whatsapp' / 'gateway.log'
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ==================== Lifecycle ====================

    async def start(self, webhook_url: str = None):
        """Start the gateway.

        Args:
            webhook_url: URL for incoming message webhooks
        """
        status = await self.client.get_session_status()

        if status == SessionStatus.STOPPED:
            await self.client.start_session(webhook_url)
            print("Session starting...")

        elif status == SessionStatus.SCAN_QR_CODE:
            print("Please scan QR code to authenticate...")
            qr = await self.client.get_qr_code()
            print(f"QR Code: {qr.get('value', 'N/A')}")

        elif status == SessionStatus.WORKING:
            print("Session already active")

        else:
            print(f"Session status: {status.value}")

    async def stop(self):
        """Stop the gateway."""
        await self.client.stop_session()
        await self.client.close()
        print("Gateway stopped")

    # ==================== Message Handling ====================

    async def _handle_message(self, message: WAHAMessage):
        """Handle incoming WhatsApp message.

        Args:
            message: Incoming message
        """
        # Skip own messages
        if message.from_me:
            return

        # Extract sender phone
        sender = self._extract_phone(message.sender or message.chat_id)

        # Check authorization
        if not self._is_authorized(sender):
            self._log_message(message, "UNAUTHORIZED")
            return

        self._log_message(message, "RECEIVED")

        # Parse command
        command = self.parse_command(message.body)

        # Execute command
        try:
            response = await self.execute_command(command, message)
            if response:
                await self.send_reply(message.chat_id, response)
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            await self.send_reply(message.chat_id, error_msg)
            self._log_message(message, f"ERROR: {e}")

    async def _handle_status_change(self, session_name: str, status: SessionStatus):
        """Handle session status change.

        Args:
            session_name: Name of session
            status: New status
        """
        print(f"Session '{session_name}' status: {status.value}")

        if status == SessionStatus.SCAN_QR_CODE:
            print("Please scan QR code in WhatsApp mobile app")

    def _is_authorized(self, phone: str) -> bool:
        """Check if phone number is authorized.

        Args:
            phone: Phone number to check

        Returns:
            True if authorized
        """
        if not self.config.allowed_numbers:
            return True  # No restrictions

        normalized = self._normalize_phone(phone)
        for allowed in self.config.allowed_numbers:
            if self._normalize_phone(allowed) == normalized:
                return True

        return False

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number for comparison."""
        return "".join(c for c in phone if c.isdigit())

    def _extract_phone(self, identifier: str) -> str:
        """Extract phone number from chat ID."""
        # Remove @c.us or @g.us suffix
        return identifier.split("@")[0]

    # ==================== Command Parsing ====================

    def parse_command(self, text: str) -> ParsedCommand:
        """Parse command from message text.

        Args:
            text: Message text

        Returns:
            ParsedCommand object
        """
        text = text.strip()

        for cmd_type, pattern in self.COMMAND_PATTERNS.items():
            match = pattern.match(text)
            if match:
                args = match.group(2) if match.lastindex >= 2 else ""
                return ParsedCommand(
                    type=cmd_type,
                    args=args.strip(),
                    raw_text=text,
                )

        # Default: treat as inbox capture if auto_capture enabled
        if self.config.auto_capture:
            return ParsedCommand(
                type=CommandType.INBOX,
                args=text,
                raw_text=text,
            )

        return ParsedCommand(
            type=CommandType.UNKNOWN,
            raw_text=text,
        )

    # ==================== Command Execution ====================

    async def execute_command(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Execute parsed command.

        Args:
            command: Parsed command
            message: Original message

        Returns:
            Response text
        """
        # Check for custom handler first
        if command.type in self._custom_handlers:
            handler = self._custom_handlers[command.type]
            if asyncio.iscoroutinefunction(handler):
                return await handler(command, message)
            return handler(command, message)

        # Built-in handlers
        handlers = {
            CommandType.TODAY: self._handle_today,
            CommandType.TOMORROW: self._handle_tomorrow,
            CommandType.INBOX: self._handle_inbox,
            CommandType.CRM_LOOKUP: self._handle_crm_lookup,
            CommandType.SEARCH: self._handle_search,
            CommandType.TASK: self._handle_task,
            CommandType.REMINDER: self._handle_reminder,
            CommandType.STATUS: self._handle_status,
            CommandType.HELP: self._handle_help,
            CommandType.UNKNOWN: self._handle_unknown,
        }

        handler = handlers.get(command.type, self._handle_unknown)
        return await handler(command, message)

    def register_handler(
        self,
        command_type: CommandType,
        handler: Callable[[ParsedCommand, WAHAMessage], Awaitable[str]],
    ):
        """Register custom command handler.

        Args:
            command_type: Command type to handle
            handler: Async handler function
        """
        self._custom_handlers[command_type] = handler

    # ==================== Built-in Handlers ====================

    async def _handle_today(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle /today command."""
        today_file = self.config.data_root / "0-personal" / "today" / f"{datetime.now().strftime('%Y-%m-%d')}.md"

        if today_file.exists():
            with open(today_file, 'r') as f:
                content = f.read()

            # Extract summary (first section)
            lines = content.split('\n')
            summary_lines = []
            for line in lines[1:30]:  # Skip title, take first 30 lines
                if line.startswith('## ') and summary_lines:
                    break
                summary_lines.append(line)

            summary = '\n'.join(summary_lines).strip()
            return f"📅 *Today's Briefing*\n\n{summary[:1500]}..."

        return "📅 No briefing generated yet. Run /today in Claude Code."

    async def _handle_tomorrow(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle /tomorrow command."""
        return "🌙 Evening wrap-up not available via WhatsApp. Run /tomorrow in Claude Code."

    async def _handle_inbox(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle inbox capture."""
        if not command.args:
            return "What would you like to capture? Send: inbox: your note"

        # Capture to inbox.org
        inbox_path = self.config.data_root / "0-personal" / "org" / "inbox.org"

        if inbox_path.exists():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n* TODO {command.args}\n:PROPERTIES:\n:CAPTURED: [{timestamp}]\n:SOURCE: whatsapp\n:END:\n"

            with open(inbox_path, 'a') as f:
                f.write(entry)

            return f"✅ Captured to inbox:\n\n_{command.args}_"

        return "❌ Could not capture - inbox.org not found"

    async def _handle_crm_lookup(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle CRM contact lookup."""
        query = command.args.lower()
        contacts_dir = self.config.data_root / "0-personal" / "contacts" / "people"

        if not contacts_dir.exists():
            return "❌ Contacts directory not found"

        matches = []
        for contact_file in contacts_dir.glob("*.md"):
            name = contact_file.stem.lower()
            if query in name:
                matches.append(contact_file)

        if not matches:
            return f"🔍 No contacts found matching: {command.args}"

        # Return top match details
        contact_file = matches[0]
        with open(contact_file, 'r') as f:
            content = f.read()

        # Extract key info
        name = contact_file.stem
        lines = content.split('\n')

        # Find role/company from frontmatter or content
        role = ""
        company = ""
        for line in lines[:30]:
            if 'role:' in line.lower():
                role = line.split(':', 1)[1].strip().strip('"')
            if 'company:' in line.lower() or 'organization:' in line.lower():
                company = line.split(':', 1)[1].strip().strip('"')

        result = f"👤 *{name}*\n"
        if role:
            result += f"📋 {role}\n"
        if company:
            result += f"🏢 {company}\n"

        if len(matches) > 1:
            result += f"\n_({len(matches)} contacts found)_"

        return result

    async def _handle_search(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle Datacortex search."""
        # This would integrate with Datacortex search
        return f"🔍 Search not yet implemented. Query: {command.args}"

    async def _handle_task(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle task creation."""
        # Same as inbox but with NEXT state
        if not command.args:
            return "What task? Send: task: your task description"

        inbox_path = self.config.data_root / "0-personal" / "org" / "inbox.org"

        if inbox_path.exists():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n* NEXT {command.args}\n:PROPERTIES:\n:CAPTURED: [{timestamp}]\n:SOURCE: whatsapp\n:END:\n"

            with open(inbox_path, 'a') as f:
                f.write(entry)

            return f"✅ Task added:\n\n_{command.args}_"

        return "❌ Could not add task"

    async def _handle_reminder(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle reminder creation."""
        return await self._handle_inbox(
            ParsedCommand(
                type=CommandType.INBOX,
                args=f"Reminder: {command.args}",
                raw_text=command.raw_text,
            ),
            message,
        )

    async def _handle_status(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle status check."""
        status = await self.client.get_session_status()
        return f"🤖 *Datacore Gateway*\n\nStatus: {status.value}\nSession: {self.config.session_name}"

    async def _handle_help(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle help request."""
        return """🤖 *Datacore WhatsApp Gateway*

*Commands:*
• `today` - Morning briefing
• `inbox: <note>` - Capture to inbox
• `task: <task>` - Add task
• `who is <name>` - CRM lookup
• `search <query>` - Search Datacortex
• `remind <text>` - Create reminder
• `status` - Check gateway status

_Or just send any text to capture to inbox._"""

    async def _handle_unknown(self, command: ParsedCommand, message: WAHAMessage) -> str:
        """Handle unknown command."""
        if self.config.auto_capture:
            return await self._handle_inbox(
                ParsedCommand(
                    type=CommandType.INBOX,
                    args=command.raw_text,
                    raw_text=command.raw_text,
                ),
                message,
            )
        return "❓ Unknown command. Send `help` for available commands."

    # ==================== Messaging ====================

    async def send_reply(self, chat_id: str, text: str):
        """Send reply message.

        Args:
            chat_id: Chat to reply to
            text: Response text
        """
        await self.client.send_message(chat_id, text)

    async def send_message(self, to: str, text: str) -> WAHAMessage:
        """Send message to phone number.

        Args:
            to: Phone number
            text: Message text

        Returns:
            Sent message
        """
        return await self.client.send_message(to, text)

    # ==================== Logging ====================

    def _log_message(self, message: WAHAMessage, status: str):
        """Log message to gateway log.

        Args:
            message: Message to log
            status: Status string
        """
        timestamp = datetime.now().isoformat()
        sender = message.sender or message.chat_id
        body = message.body[:100].replace('\n', ' ')

        log_entry = f"{timestamp} | {status} | {sender} | {body}\n"

        try:
            with open(self.log_path, 'a') as f:
                f.write(log_entry)
        except Exception:
            pass


# Synchronous wrapper
class WhatsAppGatewaySync:
    """Synchronous wrapper for WhatsAppGateway."""

    def __init__(self, *args, **kwargs):
        self._gateway = WhatsAppGateway(*args, **kwargs)

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def start(self, webhook_url: str = None):
        return self._run(self._gateway.start(webhook_url))

    def stop(self):
        return self._run(self._gateway.stop())

    def send_message(self, to: str, text: str):
        return self._run(self._gateway.send_message(to, text))

    def parse_command(self, text: str) -> ParsedCommand:
        return self._gateway.parse_command(text)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WhatsApp Gateway")
    parser.add_argument("--url", default="http://localhost:3000", help="WAHA URL")
    parser.add_argument("--start", action="store_true", help="Start gateway")
    parser.add_argument("--stop", action="store_true", help="Stop gateway")
    parser.add_argument("--send", nargs=2, metavar=("PHONE", "MESSAGE"), help="Send message")
    parser.add_argument("--test-parse", help="Test command parsing")

    args = parser.parse_args()

    gateway = WhatsAppGatewaySync(waha_url=args.url)

    if args.start:
        gateway.start()
        print("Gateway started")

    elif args.stop:
        gateway.stop()
        print("Gateway stopped")

    elif args.send:
        phone, message = args.send
        result = gateway.send_message(phone, message)
        print(f"Sent: {result}")

    elif args.test_parse:
        command = gateway.parse_command(args.test_parse)
        print(f"Command: {command.type.value}")
        print(f"Args: {command.args}")

    else:
        parser.print_help()
