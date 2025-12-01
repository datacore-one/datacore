#!/usr/bin/env python3
"""Tests for org_parser.py — Tier 0 (Parser Unification)."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'lib'))

from org_parser import (
    TODO_STATES,
    parse_heading,
    parse_deadlines,
    write_clock_entry,
    archive_task,
)


class TestTodoStates:
    """Verify all 7 GTD states are recognized."""

    def test_all_states_present(self):
        expected = ['TODO', 'NEXT', 'WAITING', 'DONE', 'CANCELLED', 'DEFERRED', 'PROJECT']
        assert TODO_STATES == expected

    def test_deferred_state_included(self):
        assert 'DEFERRED' in TODO_STATES

    @pytest.mark.parametrize('state', TODO_STATES)
    def test_parse_heading_recognizes_state(self, state):
        line = f'** {state} Some task title :tag1:'
        result = parse_heading(line)
        assert result is not None
        assert result['state'] == state
        assert result['title'] == 'Some task title'

    def test_parse_heading_no_state(self):
        line = '** Just a heading'
        result = parse_heading(line)
        assert result is not None
        assert result['state'] is None

    def test_parse_heading_with_priority(self):
        line = '** TODO [#A] Important task'
        result = parse_heading(line)
        assert result['state'] == 'TODO'
        assert result['priority'] == 'A'
        assert result['title'] == 'Important task'

    def test_parse_heading_with_tags(self):
        line = '** NEXT Do something :AI:research:'
        result = parse_heading(line)
        assert result['state'] == 'NEXT'
        assert ':AI:research:' in result['tags']

    def test_parse_heading_level(self):
        line = '*** WAITING Subtask'
        result = parse_heading(line)
        assert result['level'] == 3


class TestParseDeadlines:
    """Test deadline extraction from org files."""

    def test_basic_deadline(self, tmp_path):
        org_content = """* Focus Area
** TODO Task with deadline
DEADLINE: <2026-03-15 Sun>
:PROPERTIES:
:END:
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        result = parse_deadlines(org_file)
        assert len(result) == 1
        assert result[0]['heading'] == 'Task with deadline'
        assert result[0]['deadline_date'] == '2026-03-15'
        assert result[0]['state'] == 'TODO'
        assert result[0]['warning_days'] == 14  # default

    def test_custom_warning_days(self, tmp_path):
        org_content = """* Area
** NEXT Urgent task
DEADLINE: <2026-03-01 Sat>
:PROPERTIES:
:DEADLINE_WARNING_DAYS: 7
:END:
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        result = parse_deadlines(org_file)
        assert len(result) == 1
        assert result[0]['warning_days'] == 7

    def test_no_deadlines(self, tmp_path):
        org_content = """* Area
** TODO Task without deadline
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        result = parse_deadlines(org_file)
        assert len(result) == 0


class TestWriteClockEntry:
    """Test CLOCK entry writing."""

    def test_write_clock_basic(self, tmp_path):
        org_content = """* Area
** TODO Some task
:PROPERTIES:
:END:
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        result = write_clock_entry(
            org_file, heading_line_num=2,
            start='2026-02-22T10:00',
            end='2026-02-22T11:30',
        )
        assert result['success'] is True

        content = org_file.read_text()
        assert ':LOGBOOK:' in content
        assert 'CLOCK:' in content
        assert '1:30' in content

    def test_write_clock_existing_logbook(self, tmp_path):
        org_content = """* Area
** TODO Some task
:LOGBOOK:
CLOCK: [2026-02-21 Sat 09:00]--[2026-02-21 Sat 10:00] =>  1:00
:END:
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        result = write_clock_entry(
            org_file, heading_line_num=2,
            start='2026-02-22T14:00',
            end='2026-02-22T15:30',
        )
        assert result['success'] is True

        content = org_file.read_text()
        # New entry should be first (newest first)
        lines = content.split('\n')
        logbook_idx = next(i for i, l in enumerate(lines) if ':LOGBOOK:' in l)
        assert '2026-02-22' in lines[logbook_idx + 1]
        assert '2026-02-21' in lines[logbook_idx + 2]


class TestArchiveTask:
    """Test task archiving."""

    def test_archive_basic(self, tmp_path):
        org_content = """* Focus Area
** DONE Completed task
CLOSED: [2026-01-15 Wed 10:00]
:PROPERTIES:
:CREATED: [2026-01-01 Wed]
:END:
Some notes.
** TODO Active task
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)
        archive_file = tmp_path / 'test.org_archive'

        result = archive_task(org_file, heading_line_num=2, target_path=archive_file)
        assert result['success'] is True
        assert result['archived_heading'] == 'Completed task'

        # Source should no longer have the archived task
        source_content = org_file.read_text()
        assert 'Completed task' not in source_content
        assert 'Active task' in source_content

        # Archive should have the task with ARCHIVE_TIME
        archive_content = archive_file.read_text()
        assert 'Completed task' in archive_content
        assert ':ARCHIVE_TIME:' in archive_content

    def test_archive_default_target(self, tmp_path):
        org_content = """* Area
** DONE Old task
"""
        org_file = tmp_path / 'next_actions.org'
        org_file.write_text(org_content)

        result = archive_task(org_file, heading_line_num=2)
        assert result['success'] is True
        assert result['target'] == str(tmp_path / 'next_actions.org_archive')

    def test_archive_invalid_line(self, tmp_path):
        org_content = """* Area
** TODO Task
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        result = archive_task(org_file, heading_line_num=99)
        assert 'error' in result


class TestCLI:
    """Test CLI entry point."""

    def test_deadlines_json(self, tmp_path):
        org_content = """* Area
** TODO Task with deadline
DEADLINE: <2026-12-31 Wed>
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        import subprocess
        parser_path = Path(__file__).parent.parent / 'lib' / 'org_parser.py'
        result = subprocess.run(
            ['python3', str(parser_path), 'deadlines',
             '--file', str(org_file), '--days', '365'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) >= 1
        assert data[0]['heading'] == 'Task with deadline'

    def test_write_clock_cli(self, tmp_path):
        org_content = """* Area
** TODO Task
:PROPERTIES:
:END:
"""
        org_file = tmp_path / 'test.org'
        org_file.write_text(org_content)

        import subprocess
        parser_path = Path(__file__).parent.parent / 'lib' / 'org_parser.py'
        result = subprocess.run(
            ['python3', str(parser_path), 'write-clock',
             '--file', str(org_file),
             '--heading-line', '2',
             '--start', '2026-02-22T10:00',
             '--end', '2026-02-22T11:00'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data['success'] is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
