"""Sync must not acknowledge dropped work or cross a configured destination."""
from datetime import datetime
from unittest.mock import Mock
import pytest

from sync.engine import SyncEngine
from sync.adapters.base import ChangeType, ExternalTaskRef, OrgTask, OrgCalendarEntry, SyncResult, TaskChange, TaskState
from sync.adapters.github import GitHubAdapter
from sync.adapters.google_calendar import GoogleCalendarAdapter
from sync.conflict import ConflictStrategy, load_conflict_config


def change(external='github:allowed/repo#1', kind=ChangeType.UPDATED):
    return TaskChange(change_type=kind, org_task=OrgTask(id='one', title='one', external_id=external), timestamp=datetime.now())


def test_pull_failure_never_returns_partial_snapshot(tmp_path):
    engine = SyncEngine(str(tmp_path))
    engine.adapters = {'first': Mock(), 'second': Mock()}
    engine.adapters['first'].pull_changes.return_value = [change()]
    engine.adapters['second'].pull_changes.side_effect = OSError('private remote error')
    with pytest.raises(RuntimeError, match='no complete snapshot'):
        engine.pull_all()


@pytest.mark.parametrize('adapters, result', [({}, None), ({'github': Mock()}, SyncResult(success=False)), ({'github': Mock()}, SyncResult(success=True))])
def test_missing_or_incomplete_push_is_not_success(tmp_path, adapters, result):
    engine = SyncEngine(str(tmp_path))
    engine.adapters = adapters
    if result:
        engine.adapters['github'].push_changes.return_value = result
    assert not engine.push_all([change()]).success


def test_unimplemented_full_sync_never_advances_cursor(tmp_path):
    engine = SyncEngine(str(tmp_path))
    before = datetime(2026, 1, 1)
    engine._last_sync = before
    assert not engine.sync()['success']
    assert engine._last_sync == before


def test_layered_conflict_config_keeps_base_rules_and_rejects_invalid(tmp_path):
    config = tmp_path / '.datacore'
    config.mkdir()
    (config / 'settings.yaml').write_text('sync:\n  conflict_resolution:\n    title: ask\n    description: merge\n')
    local = config / 'settings.local.yaml'
    local.write_text('sync:\n  tasks: {enabled: true}\n')
    assert load_conflict_config(tmp_path) == {'title': ConflictStrategy.ASK, 'description': ConflictStrategy.MERGE}
    local.write_text('sync:\n  conflict_resolution: {title: typo}\n')
    with pytest.raises(ValueError):
        load_conflict_config(tmp_path)


def test_reload_cannot_keep_disabled_adapters_and_calendar_config_is_bound(tmp_path):
    config = tmp_path / '.datacore'
    config.mkdir()
    path = config / 'settings.yaml'
    path.write_text('sync:\n  adapters:\n    calendar: {enabled: true, calendar_id: team, account: secondary}\n')
    engine = SyncEngine(str(tmp_path))
    engine.load_config()
    assert engine.adapters['calendar'].calendar_id == 'team'
    assert engine.adapters['calendar'].account == 'secondary'
    path.write_text('sync:\n  adapters:\n    calendar: {enabled: false}\n')
    engine.load_config()
    assert not engine.adapters
    path.write_text('sync:\n  adapters:\n    unknown: {enabled: true}\n')
    with pytest.raises(ValueError):
        engine.load_config()
    assert not engine.adapters


def test_github_reference_substitution_never_reaches_remote():
    adapter = GitHubAdapter({'repos': [{'owner': 'allowed', 'repo': 'repo'}]})
    adapter._run_gh = Mock(side_effect=AssertionError('remote must not be reached'))
    for identity in ['github:other/repo#1', 'github:allowed/repo#1/trailing', 'github:allowed/repo#0']:
        ref = ExternalTaskRef('github', identity, '')
        assert not adapter.update_task(ref, OrgTask(id='one', title='one'))
        assert not adapter.close_task(ref)
        assert not adapter._reopen_task(ref)
    adapter._run_gh.assert_not_called()


def test_github_reopen_and_label_failures_are_reported():
    adapter = GitHubAdapter({'repos': [{'owner': 'allowed', 'repo': 'repo'}]})
    adapter._run_gh = Mock(return_value=(False, '', 'failure'))
    item = change(kind=ChangeType.STATE_CHANGED)
    assert not adapter.push_changes([item]).success
    adapter._run_gh.side_effect = [(True, '', ''), (False, '', 'label failure')]
    item.org_task.tags = [':AI:']
    assert not adapter.update_task(ExternalTaskRef('github', item.org_task.external_id, ''), item.org_task)


def test_github_failed_half_snapshot_and_capacity_are_not_success():
    adapter = GitHubAdapter({'repos': [{'owner': 'allowed', 'repo': 'repo'}]})
    adapter._run_gh = Mock(side_effect=[(True, '[]', ''), (False, '', 'failed')])
    with pytest.raises(RuntimeError):
        adapter.pull_changes()
    adapter._run_gh = Mock(return_value=(True, '[{}]'[:-1] + ',{}' * 9999 + ']', ''))
    with pytest.raises(ValueError, match='limit'):
        adapter.pull_changes()


def test_calendar_ref_substitution_and_partial_update_preservation():
    adapter = GoogleCalendarAdapter(calendar_id='allowed')
    service = Mock()
    adapter._service = service
    entry = OrgCalendarEntry(id='one', title='renamed')
    wrong = ExternalTaskRef('calendar', 'calendar:other/event', '')
    assert not adapter.close_task(wrong)
    assert not adapter.update_task(wrong, entry)
    service.events.assert_not_called()
    right = ExternalTaskRef('calendar', 'calendar:allowed/event', '')
    assert adapter.update_task(right, entry)
    service.events.return_value.update.assert_not_called()
    assert service.events.return_value.patch.call_args.kwargs['body']['summary'] == 'renamed'


def test_adapter_uses_the_engine_root_for_tag_mapping(tmp_path):
    root = tmp_path / '.datacore'
    root.mkdir()
    (root / 'tags.yaml').write_text('sync_label_mapping: {":custom:": "from-this-root"}\n')
    (root / 'settings.yaml').write_text('sync:\n  adapters:\n    github: {enabled: true, repos: []}\n')
    engine = SyncEngine(str(tmp_path))
    engine.load_config()
    assert engine.adapters['github'].label_mapping[':custom:'] == 'from-this-root'
