"""Receipt lookup preserves UTC history while honoring local calendar days."""
import importlib.util
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'hooks/command_receipt.py'
    spec = importlib.util.spec_from_file_location('receipt_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'STATE', tmp_path / 'command-runs')
    return module


@pytest.mark.parametrize('zone,stamp', [
    ('Pacific/Kiritimati', '2026-09-10T22:45:00+00:00'),
    ('America/Los_Angeles', '2026-09-10T01:00:00+00:00'),
    ('America/New_York', '2026-11-01T05:30:00+00:00'),
])
def test_utc_partition_is_found_on_local_day(receipt, monkeypatch, capsys, zone, stamp):
    with monkeypatch.context() as clock:
        clock.setenv('TZ', zone)
        time.tzset()
        fixed = datetime.fromisoformat(stamp)
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.astimezone().replace(tzinfo=None)
        class FixedDate(date):
            @classmethod
            def today(cls):
                return fixed.astimezone().date()
        clock.setattr(receipt, 'datetime', FixedDateTime)
        clock.setattr(receipt, 'date', FixedDate)
        clock.setattr(sys, 'argv', ['command_receipt.py', '--check', 'today'])
        try:
            receipt.record({'tool_name': 'Skill', 'tool_input': {'skill': 'today'}})
            original = receipt._log(fixed.date().isoformat()).read_bytes()
            assert receipt.main() == 0
            assert 'invoked 1x' in capsys.readouterr().out
            assert receipt._log(fixed.date().isoformat()).read_bytes() == original
        finally:
            clock.undo()
            time.tzset()


def test_concurrent_private_receipts_preserve_all_commands_without_arguments(receipt):
    def record(number):
        receipt.record({'tool_name': 'Skill', 'tool_input': {'skill': f'job-{number}', 'args': 'SYNTHETIC-PRIVATE-ARGUMENT'}})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(16)))
    path = receipt._log(datetime.now(timezone.utc).date().isoformat())
    text = path.read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(rows) == 16 and len({row['command'] for row in rows}) == 16
    assert 'SYNTHETIC-PRIVATE-ARGUMENT' not in text
    assert path.stat().st_mode & 0o777 == 0o600
    assert receipt.STATE.stat().st_mode & 0o777 == 0o700


def test_corrupt_receipts_fail_without_overwriting_history(receipt):
    path = receipt._log(datetime.now(timezone.utc).date().isoformat())
    path.parent.mkdir()
    path.write_text('{corrupt history')
    with pytest.raises(ValueError):
        receipt.record({'tool_name': 'Skill', 'tool_input': {'skill': 'today'}})
    with pytest.raises(ValueError):
        receipt.rows(date.today().isoformat())
    assert path.read_text() == '{corrupt history'


def test_legacy_arguments_remain_in_history_but_are_not_printed(receipt, monkeypatch, capsys):
    path = receipt._log(datetime.now(timezone.utc).date().isoformat())
    path.parent.mkdir()
    text = json.dumps({'command': 'today', 'at': datetime.now(timezone.utc).isoformat(), 'args': 'SYNTHETIC-LEGACY-SECRET'}) + '\n'
    path.write_text(text)
    monkeypatch.setattr(sys, 'argv', ['command_receipt.py', '--list'])
    assert receipt.main() == 0
    out = capsys.readouterr().out
    assert '/today' in out and 'SYNTHETIC-LEGACY-SECRET' not in out
    assert path.read_text() == text


def test_symlink_and_non_command_events_cannot_write(receipt, tmp_path):
    receipt.record({'tool_name': 'Bash', 'tool_input': {'name': 'today'}})
    assert not receipt.STATE.exists()
    external = tmp_path / 'external'
    external.mkdir()
    receipt.STATE.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        receipt.record({'tool_name': 'Skill', 'tool_input': {'skill': 'today'}})
    assert list(external.iterdir()) == []
