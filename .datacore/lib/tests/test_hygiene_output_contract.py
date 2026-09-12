"""Maintenance reports real mutations and never treats unreadable output as success."""
import json
from pathlib import Path
import subprocess
import sys

import gtd_hygiene


def test_invalid_mutator_result_is_an_error_not_a_clean_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(gtd_hygiene, '_adapter', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'not json', ''))
    result = gtd_hygiene.process_space(tmp_path / 'tasks.org', 7, False)
    assert result['archive_error']
    assert result['ensure_ids_error']
    assert result['deadlines_error']
    assert result['written_files'] == []


def test_inbox_result_requires_structured_output(tmp_path, monkeypatch):
    monkeypatch.setattr(gtd_hygiene.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'archived: 99', ''))
    result = gtd_hygiene.process_inbox(tmp_path, False)
    assert result['inbox_error']
    assert result['written_files'] == []


def test_clean_data_directory_is_independent_of_installed_code(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    result = subprocess.run([sys.executable, str(Path(gtd_hygiene.__file__)),
                             '--data-dir', str(data), '--json'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['totals']['errors'] == 0
    assert len(report['written_files']) == 1
    path = Path(report['written_files'][0])
    assert path.is_relative_to(data)
    assert json.loads(path.read_text()) == report


def test_inbox_cleanup_receipt_names_only_the_files_it_wrote(tmp_path):
    space = tmp_path / '1-fixture'
    source = space / 'org/inbox.org'
    source.parent.mkdir(parents=True)
    source.write_text('#+TITLE: Fixture\n\n* Inbox\n* DONE Closed task\nCLOSED: [2026-01-01 Thu]\n')
    script = Path(gtd_hygiene.__file__).with_name('inbox_cleanup.py')
    args = [sys.executable, str(script), str(space), '--apply', '--today', '2026-09-12', '--json']
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    archive = space / 'org/inbox-archive-2026-09-12.org'
    assert set(report['written_files']) == {str(source), str(archive)}
    assert 'Closed task' in archive.read_text()
    assert 'Closed task' not in source.read_text()
    again = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)['written_files'] == []


def test_hygiene_preserves_archived_content_and_reports_both_space_and_inbox_writes(tmp_path):
    data = tmp_path / 'data'
    org = data / '1-fixture/org'
    org.mkdir(parents=True)
    marker = org.parent / '.datacore/config.yaml'
    marker.parent.mkdir()
    marker.write_text('space:\n  name: fixture\n  type: personal\n')
    tasks = org / 'next_actions.org'
    tasks.write_text('#+TITLE: Fixture\n\n* Projects\n** Fixture\n*** DONE Finished\n'
                     'CLOSED: [2026-01-01 Thu]\n:PROPERTIES:\n:ID: finished-fixture\n:END:\n'
                     'Preserve the completed task body.\n')
    inbox = org / 'inbox.org'
    inbox.write_text('#+TITLE: Fixture inbox\n\n* Inbox\n* DONE Closed inbox task\n'
                     'CLOSED: [2026-01-01 Thu]\nKeep this inbox body too.\n')
    neighbour = org / 'unrelated.org'
    neighbour.write_text('Unrelated authored content.\n')
    result = subprocess.run([sys.executable, str(Path(gtd_hygiene.__file__)),
                             '--data-dir', str(data), '--json'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    written = {Path(path) for path in report['written_files']}
    assert tasks in written and inbox in written
    assert neighbour not in written
    archives = [path for path in written if path.parent == org and path not in (tasks, inbox)]
    assert any('Preserve the completed task body.' in path.read_text() for path in archives)
    assert any('Keep this inbox body too.' in path.read_text() for path in archives)
    assert 'Preserve the completed task body.' not in tasks.read_text()
    assert 'Keep this inbox body too.' not in inbox.read_text()
    assert neighbour.read_text() == 'Unrelated authored content.\n'
