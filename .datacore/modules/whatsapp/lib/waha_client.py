from datacore.ledger import attests
#!/usr/bin/env python3
"""
WAHA Client

Python client for WAHA (WhatsApp HTTP API) gateway.
Handles session management, message sending/receiving, and webhooks.

WAHA Docs: https://waha.devlike.pro/

Usage:
    client = WAHAClient("http://localhost:3000")
    await client.start_session()
    await client.send_message("+1234567890", "Hello!")
"""

import asyncio
import aiohttp
import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from enum import Enum


class SessionStatus(Enum):
    """WAHA session states."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    SCAN_QR_CODE = "SCAN_QR_CODE"
    WORKING = "WORKING"
    FAILED = "FAILED"


@dataclass
class WAHAMessage:
    """Incoming or outgoing WhatsApp message."""
    id: str
    chat_id: str
    from_me: bool
    body: str
    timestamp: datetime
    sender: Optional[str] = None
    sender_name: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_waha(cls, data: Dict[str, Any]) -> "WAHAMessage":
        """Create from WAHA webhook payload."""
        return cls(
            id=data.get("id", ""),
            chat_id=data.get("chatId", data.get("from", "")),
            from_me=data.get("fromMe", False),
            body=data.get("body", ""),
            timestamp=datetime.fromtimestamp(data.get("timestamp", 0)),
            sender=data.get("from"),
            sender_name=data.get("_data", {}).get("notifyName"),
            media_url=data.get("mediaUrl"),
            media_type=data.get("type") if data.get("hasMedia") else None,
            metadata=data,
        )


@dataclass
class WAHAContact:
    """WhatsApp contact."""
    id: str
    name: str
    phone: str
    is_business: bool = False
    profile_picture_url: Optional[str] = None

    @classmethod
    def from_waha(cls, data: Dict[str, Any]) -> "WAHAContact":
        """Create from WAHA API response."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", data.get("pushname", "")),
            phone=data.get("id", "").replace("@c.us", ""),
            is_business=data.get("isBusiness", False),
            profile_picture_url=data.get("profilePictureUrl"),
        )


@dataclass
class WAHAChat:
    """WhatsApp chat (individual or group)."""
    id: str
    name: str
    is_group: bool
    unread_count: int = 0
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None

    @classmethod
    def from_waha(cls, data: Dict[str, Any]) -> "WAHAChat":
        """Create from WAHA API response."""
        last_msg = data.get("lastMessage", {})
        last_ts = last_msg.get("timestamp")

        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            is_group=data.get("isGroup", False),
            unread_count=data.get("unreadCount", 0),
            last_message=last_msg.get("body"),
            last_message_at=datetime.fromtimestamp(last_ts) if last_ts else None,
        )


class WAHAClient:
    """Async client for WAHA WhatsApp HTTP API."""

    DEFAULT_SESSION = "default"

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        session_name: str = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
    ):
        """Initialize WAHA client.

        Args:
            base_url: WAHA server URL
            session_name: Session identifier (default: "default")
            api_key: Optional API key for authentication
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.session_name = session_name or self.DEFAULT_SESSION
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _ensure_session(self):
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["X-Api-Key"] = self.api_key
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=headers,
            )

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Dict[str, Any] = None,
        params: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Make API request.

        Args:
            method: HTTP method
            endpoint: API endpoint (without base URL)
            data: JSON body data
            params: Query parameters

        Returns:
            Response JSON

        Raises:
            WAHAError: On API error
        """
        await self._ensure_session()

        url = f"{self.base_url}{endpoint}"

        try:
            async with self._session.request(
                method,
                url,
                json=data,
                params=params,
            ) as response:
                result = await response.json()

                if response.status >= 400:
                    error_msg = result.get("message", str(result))
                    raise WAHAError(f"API error {response.status}: {error_msg}")

                return result

        except aiohttp.ClientError as e:
            raise WAHAError(f"Connection error: {e}")

    # ==================== Session Management ====================

    async def get_session_status(self) -> SessionStatus:
        """Get current session status.

        Returns:
            SessionStatus enum value
        """
        try:
            result = await self._request(
                "GET",
                f"/api/sessions/{self.session_name}",
            )
            status_str = result.get("status", "STOPPED")
            return SessionStatus(status_str)
        except WAHAError:
            return SessionStatus.STOPPED

    async def start_session(self, webhook_url: str = None) -> Dict[str, Any]:
        """Start WhatsApp session.

        Args:
            webhook_url: URL for incoming message webhooks

        Returns:
            Session info dict
        """
        config = {
            "name": self.session_name,
        }

        if webhook_url:
            config["config"] = {
                "webhooks": [
                    {
                        "url": webhook_url,
                        "events": ["message", "message.any"],
                    }
                ]
            }

        return await self._request("POST", "/api/sessions/start", data=config)

    async def stop_session(self) -> Dict[str, Any]:
        """Stop WhatsApp session.

        Returns:
            Result dict
        """
        return await self._request(
            "POST",
            f"/api/sessions/{self.session_name}/stop",
        )

    async def get_qr_code(self, format: str = "image") -> Dict[str, Any]:
        """Get QR code for session authentication.

        Args:
            format: "image" for base64 PNG, "raw" for raw data

        Returns:
            Dict with QR code data
        """
        return await self._request(
            "GET",
            f"/api/{self.session_name}/auth/qr",
            params={"format": format},
        )

    async def get_me(self) -> Dict[str, Any]:
        """Get info about authenticated account.

        Returns:
            Account info dict
        """
        return await self._request(
            "GET",
            f"/api/sessions/{self.session_name}/me",
        )

    # ==================== Messaging ====================

    @attests("whatsapp.sent", ref=lambda r: str(getattr(r, "id", None) or (r.get("id", "") if isinstance(r, dict) else "") or ""))
    async def send_message(
        self,
        to: str,
        text: str,
        reply_to: str = None,
    ) -> WAHAMessage:
        """Send text message.

        Args:
            to: Phone number (with country code) or chat ID
            text: Message text
            reply_to: Optional message ID to reply to

        Returns:
            Sent message object
        """
        chat_id = self._normalize_chat_id(to)

        data = {
            "chatId": chat_id,
            "text": text,
            "session": self.session_name,
        }

        if reply_to:
            data["reply_to"] = reply_to

        result = await self._request("POST", "/api/sendText", data=data)
        return WAHAMessage.from_waha(result)

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str = None,
    ) -> WAHAMessage:
        """Send image message.

        Args:
            to: Phone number or chat ID
            image_url: URL of image to send
            caption: Optional caption

        Returns:
            Sent message object
        """
        chat_id = self._normalize_chat_id(to)

        data = {
            "chatId": chat_id,
            "file": {"url": image_url},
            "session": self.session_name,
        }

        if caption:
            data["caption"] = caption

        result = await self._request("POST", "/api/sendImage", data=data)
        return WAHAMessage.from_waha(result)

    async def send_file(
        self,
        to: str,
        file_url: str,
        filename: str = None,
        caption: str = None,
    ) -> WAHAMessage:
        """Send file/document.

        Args:
            to: Phone number or chat ID
            file_url: URL of file to send
            filename: Display filename
            caption: Optional caption

        Returns:
            Sent message object
        """
        chat_id = self._normalize_chat_id(to)

        data = {
            "chatId": chat_id,
            "file": {"url": file_url},
            "session": self.session_name,
        }

        if filename:
            data["file"]["filename"] = filename
        if caption:
            data["caption"] = caption

        result = await self._request("POST", "/api/sendFile", data=data)
        return WAHAMessage.from_waha(result)

    async def send_seen(self, chat_id: str) -> Dict[str, Any]:
        """Mark chat as seen/read.

        Args:
            chat_id: Chat to mark as read

        Returns:
            Result dict
        """
        return await self._request(
            "POST",
            "/api/sendSeen",
            data={
                "chatId": self._normalize_chat_id(chat_id),
                "session": self.session_name,
            },
        )

    async def send_typing(self, chat_id: str, is_typing: bool = True) -> Dict[str, Any]:
        """Send typing indicator.

        Args:
            chat_id: Chat to show typing in
            is_typing: True for typing, False for stopped

        Returns:
            Result dict
        """
        endpoint = "/api/startTyping" if is_typing else "/api/stopTyping"
        return await self._request(
            "POST",
            endpoint,
            data={
                "chatId": self._normalize_chat_id(chat_id),
                "session": self.session_name,
            },
        )

    # ==================== Chats & Contacts ====================

    async def get_chats(self, limit: int = 50, offset: int = 0) -> List[WAHAChat]:
        """Get list of chats.

        Args:
            limit: Maximum chats to return
            offset: Pagination offset

        Returns:
            List of chat objects
        """
        result = await self._request(
            "GET",
            f"/api/{self.session_name}/chats",
            params={"limit": limit, "offset": offset},
        )

        return [WAHAChat.from_waha(c) for c in result]

    async def get_chat_messages(
        self,
        chat_id: str,
        limit: int = 50,
        download_media: bool = False,
    ) -> List[WAHAMessage]:
        """Get messages from a chat.

        Args:
            chat_id: Chat to get messages from
            limit: Maximum messages to return
            download_media: Whether to include media URLs

        Returns:
            List of message objects
        """
        result = await self._request(
            "GET",
            f"/api/{self.session_name}/chats/{self._normalize_chat_id(chat_id)}/messages",
            params={"limit": limit, "downloadMedia": download_media},
        )

        return [WAHAMessage.from_waha(m) for m in result]

    async def get_contacts(self) -> List[WAHAContact]:
        """Get all contacts.

        Returns:
            List of contact objects
        """
        result = await self._request(
            "GET",
            f"/api/{self.session_name}/contacts",
        )

        return [WAHAContact.from_waha(c) for c in result]

    async def get_contact(self, contact_id: str) -> WAHAContact:
        """Get single contact info.

        Args:
            contact_id: Phone number or contact ID

        Returns:
            Contact object
        """
        result = await self._request(
            "GET",
            f"/api/{self.session_name}/contacts/{self._normalize_chat_id(contact_id)}",
        )

        return WAHAContact.from_waha(result)

    async def check_number_exists(self, phone: str) -> bool:
        """Check if phone number is registered on WhatsApp.

        Args:
            phone: Phone number to check

        Returns:
            True if registered
        """
        try:
            result = await self._request(
                "GET",
                f"/api/{self.session_name}/contacts/check-exists",
                params={"phone": phone},
            )
            return result.get("numberExists", False)
        except WAHAError:
            return False

    # ==================== Groups ====================

    async def get_groups(self) -> List[WAHAChat]:
        """Get all group chats.

        Returns:
            List of group chat objects
        """
        result = await self._request(
            "GET",
            f"/api/{self.session_name}/groups",
        )

        return [WAHAChat.from_waha(g) for g in result]

    async def get_group_participants(self, group_id: str) -> List[Dict[str, Any]]:
        """Get participants of a group.

        Args:
            group_id: Group chat ID

        Returns:
            List of participant info dicts
        """
        return await self._request(
            "GET",
            f"/api/{self.session_name}/groups/{group_id}/participants",
        )

    # ==================== Utilities ====================

    def _normalize_chat_id(self, identifier: str) -> str:
        """Normalize phone number or chat ID to WAHA format.

        Args:
            identifier: Phone number or chat ID

        Returns:
            Normalized chat ID (e.g., "1234567890@c.us")
        """
        # Already in chat ID format
        if "@" in identifier:
            return identifier

        # Clean phone number
        cleaned = "".join(c for c in identifier if c.isdigit() or c == "+")
        cleaned = cleaned.lstrip("+")

        # Individual chat
        return f"{cleaned}@c.us"

    def normalize_phone(self, phone: str) -> str:
        """Normalize phone number to consistent format.

        Args:
            phone: Phone number in any format

        Returns:
            Normalized phone (digits only, no +)
        """
        return "".join(c for c in phone if c.isdigit())


class WAHAError(Exception):
    """WAHA API error."""
    pass


class WAHAWebhookHandler:
    """Handle incoming WAHA webhooks.

    Usage with aiohttp:
        handler = WAHAWebhookHandler()
        handler.on_message(my_callback)

        app = aiohttp.web.Application()
        app.router.add_post('/webhook', handler.handle_webhook)
    """

    def __init__(self):
        """Initialize webhook handler."""
        self._message_handlers: List[Callable] = []
        self._status_handlers: List[Callable] = []

    def on_message(self, handler: Callable[[WAHAMessage], None]):
        """Register message handler.

        Args:
            handler: Async function to call on new message
        """
        self._message_handlers.append(handler)

    def on_status_change(self, handler: Callable[[str, SessionStatus], None]):
        """Register session status handler.

        Args:
            handler: Async function to call on status change
        """
        self._status_handlers.append(handler)

    async def handle_webhook(self, request) -> Dict[str, Any]:
        """Handle incoming webhook request.

        Args:
            request: aiohttp request object

        Returns:
            Response dict
        """
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}

        event = data.get("event", "")

        # Message events
        if event in ("message", "message.any"):
            message = WAHAMessage.from_waha(data.get("payload", {}))

            for handler in self._message_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(message)
                    else:
                        handler(message)
                except Exception as e:
                    print(f"Message handler error: {e}")

        # Session status events
        elif event == "session.status":
            payload = data.get("payload", {})
            session_name = payload.get("name", "")
            status = SessionStatus(payload.get("status", "STOPPED"))

            for handler in self._status_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(session_name, status)
                    else:
                        handler(session_name, status)
                except Exception as e:
                    print(f"Status handler error: {e}")

        return {"status": "ok"}


# Synchronous wrapper for non-async contexts
class WAHAClientSync:
    """Synchronous wrapper for WAHAClient."""

    def __init__(self, *args, **kwargs):
        """Initialize with same args as WAHAClient."""
        self._client = WAHAClient(*args, **kwargs)

    def _run(self, coro):
        """Run coroutine synchronously."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def get_session_status(self) -> SessionStatus:
        return self._run(self._client.get_session_status())

    def start_session(self, webhook_url: str = None) -> Dict[str, Any]:
        return self._run(self._client.start_session(webhook_url))

    def stop_session(self) -> Dict[str, Any]:
        return self._run(self._client.stop_session())

    def get_qr_code(self, format: str = "image") -> Dict[str, Any]:
        return self._run(self._client.get_qr_code(format))

    @attests("whatsapp.sent", ref=lambda r: str(getattr(r, "id", None) or (r.get("id", "") if isinstance(r, dict) else "") or ""))
    def send_message(self, to: str, text: str, reply_to: str = None) -> WAHAMessage:
        return self._run(self._client.send_message(to, text, reply_to))

    def send_image(self, to: str, image_url: str, caption: str = None) -> WAHAMessage:
        return self._run(self._client.send_image(to, image_url, caption))

    def get_chats(self, limit: int = 50, offset: int = 0) -> List[WAHAChat]:
        return self._run(self._client.get_chats(limit, offset))

    def get_chat_messages(self, chat_id: str, limit: int = 50) -> List[WAHAMessage]:
        return self._run(self._client.get_chat_messages(chat_id, limit))

    def get_contacts(self) -> List[WAHAContact]:
        return self._run(self._client.get_contacts())

    def check_number_exists(self, phone: str) -> bool:
        return self._run(self._client.check_number_exists(phone))

    def close(self):
        self._run(self._client.close())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WAHA Client")
    parser.add_argument("--url", default="http://localhost:3000", help="WAHA server URL")
    parser.add_argument("--status", action="store_true", help="Check session status")
    parser.add_argument("--start", action="store_true", help="Start session")
    parser.add_argument("--stop", action="store_true", help="Stop session")
    parser.add_argument("--qr", action="store_true", help="Get QR code")
    parser.add_argument("--chats", action="store_true", help="List chats")
    parser.add_argument("--contacts", action="store_true", help="List contacts")
    parser.add_argument("--send", nargs=2, metavar=("PHONE", "MESSAGE"), help="Send message")

    args = parser.parse_args()

    client = WAHAClientSync(args.url)

    try:
        if args.status:
            status = client.get_session_status()
            print(f"Session status: {status.value}")

        elif args.start:
            result = client.start_session()
            print(f"Session started: {result}")

        elif args.stop:
            result = client.stop_session()
            print(f"Session stopped: {result}")

        elif args.qr:
            result = client.get_qr_code()
            print(f"QR Code: {result}")

        elif args.chats:
            chats = client.get_chats()
            print(f"\n=== Chats ({len(chats)}) ===")
            for chat in chats[:20]:
                group_tag = "[GROUP] " if chat.is_group else ""
                print(f"  {group_tag}{chat.name}: {chat.unread_count} unread")

        elif args.contacts:
            contacts = client.get_contacts()
            print(f"\n=== Contacts ({len(contacts)}) ===")
            for contact in contacts[:20]:
                print(f"  {contact.name} ({contact.phone})")

        elif args.send:
            phone, message = args.send
            result = client.send_message(phone, message)
            print(f"Message sent: {result.id}")

        else:
            parser.print_help()

    finally:
        client.close()
