from datacore.ledger import attests
#!/usr/bin/env python3
"""
WhatsApp Notifications

Proactive notification service for Datacore via WhatsApp.
Sends briefings, reminders, and alerts to configured number.

Usage:
    notifier = WhatsAppNotifications(gateway, owner_number="+1234567890")
    await notifier.send_morning_briefing()
"""

import asyncio
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from waha_client import WAHAClient, SessionStatus
from whatsapp_gateway import WhatsAppGateway, GatewayConfig


@dataclass
class NotificationConfig:
    """Notification configuration."""
    owner_number: str = ""
    morning_briefing_enabled: bool = False
    morning_briefing_time: str = "07:00"
    follow_up_reminders_enabled: bool = False
    follow_up_reminder_time: str = "09:00"
    nightshift_alerts_enabled: bool = True
    dormant_contact_days: int = 14
    data_root: Path = None

    def __post_init__(self):
        if self.data_root is None:
            self.data_root = Path.home() / "Data"

    @classmethod
    def from_yaml(cls, path: Path) -> "NotificationConfig":
        """Load config from YAML."""
        if path.exists():
            with open(path, 'r') as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()


class WhatsAppNotifications:
    """Proactive WhatsApp notifications for Datacore."""

    def __init__(
        self,
        gateway: WhatsAppGateway = None,
        waha_url: str = "http://localhost:3000",
        owner_number: str = None,
        config: NotificationConfig = None,
        data_root: Path = None,
    ):
        """Initialize notification service.

        Args:
            gateway: Existing gateway instance (optional)
            waha_url: WAHA server URL (if no gateway)
            owner_number: Phone number to send notifications to
            config: Notification configuration
            data_root: Path to ~/Data
        """
        self.config = config or NotificationConfig()

        if owner_number:
            self.config.owner_number = owner_number
        if data_root:
            self.config.data_root = Path(data_root)

        # Use existing gateway or create client
        if gateway:
            self.gateway = gateway
            self.client = gateway.client
        else:
            self.gateway = None
            self.client = WAHAClient(base_url=waha_url)

    async def _ensure_connected(self) -> bool:
        """Ensure WhatsApp session is active.

        Returns:
            True if connected
        """
        try:
            status = await self.client.get_session_status()
            return status == SessionStatus.WORKING
        except Exception:
            return False

    @attests("whatsapp.sent", ref=lambda r: str(getattr(r, "id", None) or (r.get("id", "") if isinstance(r, dict) else "") or ""))
    async def send_notification(self, text: str, to: str = None) -> bool:
        """Send notification message.

        Args:
            text: Message text
            to: Phone number (default: owner_number)

        Returns:
            True if sent successfully
        """
        if not await self._ensure_connected():
            print("WhatsApp session not active")
            return False

        recipient = to or self.config.owner_number
        if not recipient:
            print("No recipient configured")
            return False

        try:
            await self.client.send_message(recipient, text)
            return True
        except Exception as e:
            print(f"Failed to send notification: {e}")
            return False

    # ==================== Morning Briefing ====================

    async def send_morning_briefing(self) -> bool:
        """Send morning briefing summary via WhatsApp.

        Returns:
            True if sent
        """
        briefing = self._generate_morning_briefing()
        if not briefing:
            return False

        return await self.send_notification(briefing)

    def _generate_morning_briefing(self) -> Optional[str]:
        """Generate morning briefing text.

        Returns:
            Briefing text or None
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_file = self.config.data_root / "0-personal" / "today" / f"{today_str}.md"

        # Try to load today's briefing
        if today_file.exists():
            return self._extract_briefing_summary(today_file)

        # Generate minimal briefing
        return self._generate_minimal_briefing()

    def _extract_briefing_summary(self, today_file: Path) -> str:
        """Extract summary from today's briefing file."""
        with open(today_file, 'r') as f:
            content = f.read()

        lines = content.split('\n')
        summary_parts = []

        # Extract date and key sections
        in_section = None
        section_content = []

        for line in lines:
            if line.startswith('# '):
                # Title
                continue
            elif line.startswith('## '):
                # Save previous section
                if in_section and section_content:
                    summary_parts.append(f"*{in_section}*\n" + '\n'.join(section_content[:5]))
                    section_content = []

                in_section = line[3:].strip()

                # Only include key sections
                if in_section.lower() not in ['priorities', 'calendar', 'tasks', 'focus']:
                    in_section = None
            elif in_section and line.strip():
                section_content.append(line)

        # Add last section
        if in_section and section_content:
            summary_parts.append(f"*{in_section}*\n" + '\n'.join(section_content[:5]))

        if not summary_parts:
            return None

        today_formatted = datetime.now().strftime("%A, %B %d")
        header = f"📅 *Good morning!*\n_{today_formatted}_\n"

        return header + "\n\n".join(summary_parts[:3])

    def _generate_minimal_briefing(self) -> str:
        """Generate minimal briefing from org files."""
        today_formatted = datetime.now().strftime("%A, %B %d")
        header = f"📅 *Good morning!*\n_{today_formatted}_\n\n"

        parts = []

        # Check for NEXT tasks
        next_actions_path = self.config.data_root / "0-personal" / "org" / "next_actions.org"
        if next_actions_path.exists():
            tasks = self._extract_next_tasks(next_actions_path)
            if tasks:
                parts.append("*Today's Tasks:*\n" + '\n'.join(f"• {t}" for t in tasks[:5]))

        # Check inbox
        inbox_path = self.config.data_root / "0-personal" / "org" / "inbox.org"
        if inbox_path.exists():
            inbox_count = self._count_inbox_items(inbox_path)
            if inbox_count > 0:
                parts.append(f"📥 {inbox_count} items in inbox")

        if not parts:
            parts.append("No tasks scheduled. Run /today for full briefing.")

        return header + "\n\n".join(parts)

    def _extract_next_tasks(self, next_actions_path: Path, max_tasks: int = 5) -> List[str]:
        """Extract NEXT tasks from org file."""
        tasks = []

        with open(next_actions_path, 'r') as f:
            for line in f:
                if '* NEXT ' in line:
                    task = line.split('* NEXT ', 1)[1].strip()
                    # Remove tags
                    task = task.split(':')[0].strip()
                    tasks.append(task)
                    if len(tasks) >= max_tasks:
                        break

        return tasks

    def _count_inbox_items(self, inbox_path: Path) -> int:
        """Count items in inbox."""
        count = 0
        with open(inbox_path, 'r') as f:
            for line in f:
                if line.strip().startswith('* '):
                    count += 1
        return count

    # ==================== Follow-up Reminders ====================

    async def send_follow_up_reminders(self) -> int:
        """Send reminders for contacts needing follow-up.

        Returns:
            Number of reminders sent
        """
        dormant = self._find_dormant_contacts()
        if not dormant:
            return 0

        # Send consolidated reminder
        message = "👥 *Contact Follow-up Reminder*\n\n"
        message += "These contacts haven't been contacted recently:\n\n"

        for contact in dormant[:5]:
            days = contact['days_since_contact']
            message += f"• *{contact['name']}*\n"
            message += f"  _{days} days since last contact_\n"

        if len(dormant) > 5:
            message += f"\n_...and {len(dormant) - 5} more_"

        await self.send_notification(message)
        return len(dormant)

    def _find_dormant_contacts(self) -> List[Dict[str, Any]]:
        """Find contacts needing follow-up.

        Returns:
            List of dormant contact dicts
        """
        contacts_dir = self.config.data_root / "0-personal" / "contacts" / "people"
        if not contacts_dir.exists():
            return []

        dormant = []
        cutoff = datetime.now() - timedelta(days=self.config.dormant_contact_days)

        for contact_file in contacts_dir.glob("*.md"):
            contact_info = self._check_contact_dormancy(contact_file, cutoff)
            if contact_info:
                dormant.append(contact_info)

        # Sort by most dormant
        dormant.sort(key=lambda x: x['days_since_contact'], reverse=True)
        return dormant

    def _check_contact_dormancy(self, contact_file: Path, cutoff: datetime) -> Optional[Dict[str, Any]]:
        """Check if contact is dormant.

        Args:
            contact_file: Path to contact file
            cutoff: Cutoff date for dormancy

        Returns:
            Contact info dict if dormant, None otherwise
        """
        try:
            with open(contact_file, 'r') as f:
                content = f.read()

            # Parse frontmatter
            if not content.startswith('---'):
                return None

            end = content.find('---', 3)
            if end < 0:
                return None

            frontmatter = content[3:end]

            # Check relevance (only remind for high-relevance contacts)
            relevance = 0
            last_contact_str = None

            for line in frontmatter.split('\n'):
                if 'relevance:' in line:
                    try:
                        relevance = int(line.split(':')[1].strip())
                    except (ValueError, IndexError):
                        pass
                elif 'last_contact:' in line:
                    last_contact_str = line.split(':', 1)[1].strip()

            # Only track high-relevance contacts
            if relevance < 3:
                return None

            if not last_contact_str:
                return None

            # Parse last contact date
            try:
                last_contact = datetime.strptime(last_contact_str.strip('"'), "%Y-%m-%d")
            except ValueError:
                return None

            if last_contact < cutoff:
                days = (datetime.now() - last_contact).days
                return {
                    'name': contact_file.stem.split(' | ')[0],
                    'file': contact_file,
                    'last_contact': last_contact,
                    'days_since_contact': days,
                    'relevance': relevance,
                }

        except Exception:
            pass

        return None

    # ==================== Nightshift Alerts ====================

    async def send_nightshift_completion(self, task_name: str, result: str) -> bool:
        """Send notification when nightshift task completes.

        Args:
            task_name: Name of completed task
            result: Task result summary

        Returns:
            True if sent
        """
        message = f"🌙 *Nightshift Complete*\n\n"
        message += f"*Task:* {task_name}\n\n"
        message += f"*Result:*\n{result[:500]}"

        if len(result) > 500:
            message += "\n\n_[Truncated - see full results in Datacore]_"

        return await self.send_notification(message)

    async def send_nightshift_error(self, task_name: str, error: str) -> bool:
        """Send notification when nightshift task fails.

        Args:
            task_name: Name of failed task
            error: Error message

        Returns:
            True if sent
        """
        message = f"❌ *Nightshift Error*\n\n"
        message += f"*Task:* {task_name}\n\n"
        message += f"*Error:*\n{error[:300]}"

        return await self.send_notification(message)

    # ==================== Custom Alerts ====================

    async def send_alert(self, title: str, body: str, emoji: str = "🔔") -> bool:
        """Send custom alert.

        Args:
            title: Alert title
            body: Alert body
            emoji: Emoji prefix

        Returns:
            True if sent
        """
        message = f"{emoji} *{title}*\n\n{body}"
        return await self.send_notification(message)

    async def send_task_reminder(self, task: str, due: datetime = None) -> bool:
        """Send task reminder.

        Args:
            task: Task description
            due: Due date (optional)

        Returns:
            True if sent
        """
        message = f"⏰ *Task Reminder*\n\n{task}"

        if due:
            due_str = due.strftime("%Y-%m-%d %H:%M")
            message += f"\n\n_Due: {due_str}_"

        return await self.send_notification(message)

    # ==================== Scheduled Notifications ====================

    async def run_scheduled(self):
        """Run scheduled notifications based on config.

        This should be called by a scheduler (cron, etc.)
        """
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        # Morning briefing
        if (self.config.morning_briefing_enabled and
            current_time == self.config.morning_briefing_time):
            await self.send_morning_briefing()

        # Follow-up reminders
        if (self.config.follow_up_reminders_enabled and
            current_time == self.config.follow_up_reminder_time):
            await self.send_follow_up_reminders()


# Synchronous wrapper
class WhatsAppNotificationsSync:
    """Synchronous wrapper for WhatsAppNotifications."""

    def __init__(self, *args, **kwargs):
        self._notifier = WhatsAppNotifications(*args, **kwargs)

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @attests("whatsapp.sent", ref=lambda r: str(getattr(r, "id", None) or (r.get("id", "") if isinstance(r, dict) else "") or ""))
    def send_notification(self, text: str, to: str = None) -> bool:
        return self._run(self._notifier.send_notification(text, to))

    def send_morning_briefing(self) -> bool:
        return self._run(self._notifier.send_morning_briefing())

    def send_follow_up_reminders(self) -> int:
        return self._run(self._notifier.send_follow_up_reminders())

    def send_nightshift_completion(self, task_name: str, result: str) -> bool:
        return self._run(self._notifier.send_nightshift_completion(task_name, result))

    def send_alert(self, title: str, body: str, emoji: str = "🔔") -> bool:
        return self._run(self._notifier.send_alert(title, body, emoji))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WhatsApp Notifications")
    parser.add_argument("--url", default="http://localhost:3000", help="WAHA URL")
    parser.add_argument("--to", required=True, help="Phone number to send to")
    parser.add_argument("--briefing", action="store_true", help="Send morning briefing")
    parser.add_argument("--follow-up", action="store_true", help="Send follow-up reminders")
    parser.add_argument("--alert", nargs=2, metavar=("TITLE", "BODY"), help="Send custom alert")
    parser.add_argument("--test", action="store_true", help="Send test notification")

    args = parser.parse_args()

    notifier = WhatsAppNotificationsSync(
        waha_url=args.url,
        owner_number=args.to,
    )

    if args.briefing:
        result = notifier.send_morning_briefing()
        print(f"Briefing sent: {result}")

    elif args.follow_up:
        count = notifier.send_follow_up_reminders()
        print(f"Sent {count} follow-up reminders")

    elif args.alert:
        title, body = args.alert
        result = notifier.send_alert(title, body)
        print(f"Alert sent: {result}")

    elif args.test:
        result = notifier.send_notification("🧪 Test notification from Datacore WhatsApp module")
        print(f"Test sent: {result}")

    else:
        parser.print_help()
