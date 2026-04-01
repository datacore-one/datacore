"""
Datacore WhatsApp Module

Native WhatsApp integration for Datacore:
- CRM adapter for contact/interaction tracking
- Export parser for .txt chat exports
- WAHA gateway for bidirectional messaging
- Notifications for proactive alerts
"""

from .whatsapp_export_parser import WhatsAppExportParser, ChatExport, Message
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_contact_creator import WhatsAppContactCreator
from .waha_client import (
    WAHAClient,
    WAHAClientSync,
    WAHAMessage,
    WAHAContact,
    WAHAChat,
    WAHAWebhookHandler,
    WAHAError,
    SessionStatus,
)
from .whatsapp_gateway import (
    WhatsAppGateway,
    WhatsAppGatewaySync,
    GatewayConfig,
    ParsedCommand,
    CommandType,
)
from .whatsapp_notifications import (
    WhatsAppNotifications,
    WhatsAppNotificationsSync,
    NotificationConfig,
)

__all__ = [
    # Export Parser
    'WhatsAppExportParser',
    'ChatExport',
    'Message',

    # CRM Adapter
    'WhatsAppAdapter',

    # Contact Creator
    'WhatsAppContactCreator',

    # WAHA Client
    'WAHAClient',
    'WAHAClientSync',
    'WAHAMessage',
    'WAHAContact',
    'WAHAChat',
    'WAHAWebhookHandler',
    'WAHAError',
    'SessionStatus',

    # Gateway
    'WhatsAppGateway',
    'WhatsAppGatewaySync',
    'GatewayConfig',
    'ParsedCommand',
    'CommandType',

    # Notifications
    'WhatsAppNotifications',
    'WhatsAppNotificationsSync',
    'NotificationConfig',
]
