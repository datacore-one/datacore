"""Calendar failures must not execute credential files or truncate snapshots."""
import pickle
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from sync.adapters import gcal_auth, google_calendar
from sync.adapters.google_calendar import GoogleCalendarAdapter


def _mark(path):
    Path(path).write_text("executed")


class _Payload:
    def __init__(self, path):
        self.path = path
    def __reduce__(self):
        return _mark, (str(self.path),)


@pytest.mark.parametrize("module", [gcal_auth, google_calendar])
def test_legacy_credential_is_never_executed(tmp_path, monkeypatch, module):
    legacy = tmp_path / "token.pickle"
    legacy.write_bytes(pickle.dumps(_Payload(tmp_path / "executed")))
    original = legacy.read_bytes()
    monkeypatch.setattr(module, "CREDS_DIR", tmp_path)
    monkeypatch.setattr(module, "_LEGACY_PICKLE_FILE", legacy)
    monkeypatch.setattr(module, "_DEFAULT_TOKEN" if module is gcal_auth else "TOKEN_FILE", tmp_path / "new.json")
    if module is gcal_auth:
        module._migrate_pickle_token()
    else:
        GoogleCalendarAdapter()._migrate_pickle_token()
    assert not (tmp_path / "executed").exists()
    assert legacy.read_bytes() == original


@pytest.mark.parametrize("account", ["x/../../outside", "../outside", "x\\outside", "\x00"])
def test_both_calendar_clients_reject_path_accounts(account):
    with pytest.raises(ValueError):
        gcal_auth._token_file_for(account)
    with pytest.raises(ValueError):
        GoogleCalendarAdapter(account=account)._token_file()


def _event(number):
    return {"id": str(number), "summary": str(number),
            "start": {"date": "2026-09-10"}, "end": {"date": "2026-09-11"},
            "created": "2026-09-10T00:00:00Z", "updated": "2026-09-10T00:00:00Z"}


def test_all_calendar_pages_are_preserved():
    adapter = GoogleCalendarAdapter()
    service = Mock()
    service.events.return_value.list.return_value.execute.side_effect = [
        {"items": [_event(1)], "nextPageToken": "next"}, {"items": [_event(2)]}]
    adapter._service = service
    assert len(adapter.pull_events()) == 2
    assert service.events.return_value.list.call_args.kwargs["pageToken"] == "next"


def test_failed_or_malformed_page_never_replaces_existing_calendar(tmp_path):
    adapter = GoogleCalendarAdapter()
    service = Mock()
    service.events.return_value.list.return_value.execute.side_effect = [
        {"items": [_event(1)], "nextPageToken": "next"}, OSError("dependency failed")]
    adapter._service = service
    path = tmp_path / "calendar.org"
    path.write_text("retained calendar and notes\n")
    with pytest.raises(Exception):
        adapter.sync_to_org_file(str(path))
    assert path.read_text() == "retained calendar and notes\n"


def test_unimplemented_push_does_not_acknowledge_work():
    result = GoogleCalendarAdapter().push_changes([Mock()])
    assert not result.success
    assert result.errors


def test_successful_calendar_snapshot_archives_previous_bytes(tmp_path):
    adapter = GoogleCalendarAdapter()
    service = Mock()
    service.events.return_value.list.return_value.execute.return_value = {"items": [_event(1)]}
    adapter._service = service
    path = tmp_path / "calendar.org"
    path.write_text("retained old snapshot and notes\n")
    assert adapter.sync_to_org_file(str(path)) == 1
    assert path.read_text().startswith("#+TITLE: Calendar\n")
    assert any(p.read_text() == "retained old snapshot and notes\n" for p in (tmp_path / ".calendar-backups").iterdir())


def test_empty_successful_snapshot_clears_stale_events_but_keeps_backup(tmp_path):
    adapter = GoogleCalendarAdapter()
    service = Mock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    adapter._service = service
    path = tmp_path / "calendar.org"
    path.write_text("** removed event\n")
    assert adapter.sync_to_org_file(str(path)) == 0
    assert "removed event" not in path.read_text()
    assert next((tmp_path / ".calendar-backups").iterdir()).read_text() == "** removed event\n"


def test_external_calendar_text_cannot_introduce_org_tasks_or_directives():
    import json
    import re
    from sync.adapters.base import OrgCalendarEntry
    from org_literal import scalar
    attack = 'TODO injected :AI:\n* TODO forged :AI:\r#+CALL: malicious()\n:END:\n[[elisp:bad][open]]'
    entry = OrgCalendarEntry(id='one', title=attack, body=attack,
                             external_id=attack, external_url=attack,
                             location=attack, attendees=[attack])
    text = '\n'.join(GoogleCalendarAdapter()._entry_to_org_lines(entry))
    assert len(re.findall(r'^\*+ ', text, re.M)) == 1
    assert not re.search(r'^\*+ (TODO|NEXT|WAITING)\b', text, re.M)
    assert not re.search(r'^#\+', text, re.M)
    assert text.count('\n:END:') == 1
    assert json.loads('"' + scalar(attack) + '"') == attack
    assert '[[elisp:' not in text.split('\n:END:')[0]


def test_all_day_range_survives_calendar_round_trip():
    adapter = GoogleCalendarAdapter()
    event = _event(1)
    event['start'] = {'date': '2028-02-28'}
    event['end'] = {'date': '2028-03-02'}
    entry = adapter._event_to_org_entry(adapter._parse_event(event))
    assert entry.is_all_day
    restored = adapter._org_entry_to_event(entry)
    assert restored['start'] == event['start'] and restored['end'] == event['end']
    text = '\n'.join(adapter._entry_to_org_lines(entry))
    assert '<2028-02-28 Mon>--<2028-03-01 Wed>' in text


def test_midnight_and_overnight_events_keep_timed_semantics():
    adapter = GoogleCalendarAdapter()
    event = _event(1)
    event['start'] = {'dateTime': '2026-09-10T00:00:00+00:00'}
    event['end'] = {'dateTime': '2026-09-11T01:30:00+00:00'}
    entry = adapter._event_to_org_entry(adapter._parse_event(event))
    assert not entry.is_all_day
    assert adapter._org_entry_to_event(entry)['start'] == event['start']
    assert '<2026-09-10 Thu 00:00>--<2026-09-11 Fri 01:30>' in '\n'.join(adapter._entry_to_org_lines(entry))
