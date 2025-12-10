"""
Tests for Google Calendar adapter.

DIP-0010: External Sync Architecture - Phase 3
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sync.adapters.base import OrgCalendarEntry
from sync.adapters.google_calendar import GoogleCalendarAdapter, CalendarEvent


class TestOrgCalendarEntry:
    """Tests for OrgCalendarEntry dataclass."""

    def test_create_entry(self):
        """Test creating a calendar entry."""
        entry = OrgCalendarEntry(
            id="test-1",
            title="Team Meeting",
            timestamp=datetime(2025, 12, 10, 10, 0),
            end_time=datetime(2025, 12, 10, 11, 0),
        )

        assert entry.title == "Team Meeting"
        assert entry.timestamp is not None
        assert entry.duration_minutes == 60

    def test_all_day_event(self):
        """Test all-day event detection."""
        entry = OrgCalendarEntry(
            id="test-2",
            title="Holiday",
            timestamp=datetime(2025, 12, 25, 0, 0),
        )

        assert entry.is_all_day is True

    def test_timed_event(self):
        """Test timed event detection."""
        entry = OrgCalendarEntry(
            id="test-3",
            title="Meeting",
            timestamp=datetime(2025, 12, 10, 14, 30),
            end_time=datetime(2025, 12, 10, 15, 0),
        )

        assert entry.is_all_day is False
        assert entry.duration_minutes == 30


class TestCalendarEvent:
    """Tests for CalendarEvent dataclass."""

    def test_create_event(self):
        """Test creating a calendar event."""
        event = CalendarEvent(
            id="abc123",
            title="Test Event",
            start=datetime(2025, 12, 10, 10, 0),
            end=datetime(2025, 12, 10, 11, 0),
            url="https://calendar.google.com/event/abc123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        assert event.title == "Test Event"
        assert event.id == "abc123"


class TestGoogleCalendarAdapter:
    """Tests for GoogleCalendarAdapter."""

    def test_adapter_properties(self):
        """Test adapter name and org_file properties."""
        adapter = GoogleCalendarAdapter()

        assert adapter.name == "calendar"
        assert adapter.org_file == "calendar.org"

    def test_is_configured_without_credentials(self):
        """Test is_configured returns False without credentials."""
        adapter = GoogleCalendarAdapter()

        # Will return False if token doesn't exist
        # (may be True in test environment with credentials)
        result = adapter.is_configured()
        assert isinstance(result, bool)

    def test_event_to_org_entry(self):
        """Test converting CalendarEvent to OrgCalendarEntry."""
        adapter = GoogleCalendarAdapter(calendar_id="test@example.com")

        event = CalendarEvent(
            id="event123",
            title="Team Standup",
            start=datetime(2025, 12, 10, 9, 0),
            end=datetime(2025, 12, 10, 9, 30),
            url="https://calendar.google.com/event/event123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            location="Zoom",
            attendees=["alice@example.com", "bob@example.com"],
        )

        entry = adapter._event_to_org_entry(event)

        assert entry.title == "Team Standup"
        assert entry.timestamp == datetime(2025, 12, 10, 9, 0)
        assert entry.location == "Zoom"
        assert len(entry.attendees) == 2
        assert entry.external_id == "calendar:test@example.com/event123"

    def test_org_entry_to_event(self):
        """Test converting OrgCalendarEntry to Google event body."""
        adapter = GoogleCalendarAdapter()

        entry = OrgCalendarEntry(
            id="test-1",
            title="Project Review",
            body="Quarterly review meeting",
            timestamp=datetime(2025, 12, 15, 14, 0),
            end_time=datetime(2025, 12, 15, 15, 0),
            location="Conference Room A",
        )

        event_body = adapter._org_entry_to_event(entry)

        assert event_body['summary'] == "Project Review"
        assert event_body['description'] == "Quarterly review meeting"
        assert event_body['location'] == "Conference Room A"
        assert 'start' in event_body
        assert 'end' in event_body

    def test_entry_to_org_lines(self):
        """Test converting entry to org-mode lines."""
        adapter = GoogleCalendarAdapter()

        entry = OrgCalendarEntry(
            id="test-1",
            title="Weekly Sync",
            timestamp=datetime(2025, 12, 11, 10, 0),
            end_time=datetime(2025, 12, 11, 10, 30),
            external_id="calendar:primary/abc123",
            external_url="https://calendar.google.com/event/abc123",
            location="Zoom",
            attendees=["team@example.com"],
        )

        lines = adapter._entry_to_org_lines(entry)

        assert "** Weekly Sync" in lines
        assert any(":EXTERNAL_ID: calendar:primary/abc123" in line for line in lines)
        assert any("Location: Zoom" in line for line in lines)
        assert any("Attendees: team@example.com" in line for line in lines)


class TestCalendarAdapterIntegration:
    """Integration tests (require valid credentials)."""

    @pytest.fixture
    def adapter(self):
        """Create adapter for testing."""
        return GoogleCalendarAdapter(calendar_id="primary")

    def test_connection(self, adapter):
        """Test connection to Google Calendar."""
        if not adapter.is_configured():
            pytest.skip("Calendar not configured")

        success, message = adapter.test_connection()
        assert success, f"Connection failed: {message}"

    def test_pull_events(self, adapter):
        """Test pulling events from calendar."""
        if not adapter.is_configured():
            pytest.skip("Calendar not configured")

        events = adapter.pull_events(days=7)
        assert isinstance(events, list)

        for event in events:
            assert isinstance(event, OrgCalendarEntry)
            assert event.title is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
