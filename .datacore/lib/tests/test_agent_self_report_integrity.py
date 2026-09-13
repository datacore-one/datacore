"""A self-report is scoped, honest diagnostic evidence, never an execution grant."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import agent_self_report as report


@pytest.fixture
def tree(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setenv('DATACORE_ACTOR', 'fixture')
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'private'))
    monkeypatch.setattr(report, '_AGENT_STATE_DIR', data / '.datacore/state/agents')
    return data


def test_self_report_cannot_claim_a_different_actor(tree):
    with pytest.raises(ValueError):
        report.write_self_report('other', 'Other', 'ok')
    assert not (tree / '.datacore').exists()


@pytest.mark.parametrize('slug', ['../escape', 'ABSOLUTE_FIXTURE', '.', 'nested/actor'])
def test_report_identity_cannot_select_a_path(tree, slug):
    if slug == 'ABSOLUTE_FIXTURE':
        slug = str(tree.parent / 'escape')
    with pytest.raises(ValueError):
        report.write_self_report(slug, 'Fixture')


def test_unknown_status_cannot_become_success(tree):
    with pytest.raises(ValueError):
        report.write_self_report('fixture', 'Fixture', 'unverified')


def test_aliased_report_is_preserved_and_refused(tree, tmp_path):
    private = tmp_path / 'private.json'
    private.write_text('PRIVATE-fixture')
    path = tree / '.datacore/state/agents/fixture.json'
    path.parent.mkdir(parents=True)
    path.symlink_to(private)
    with pytest.raises((ValueError, OSError)):
        report.write_self_report('fixture', 'Fixture')
    assert path.is_symlink() and private.read_text() == 'PRIVATE-fixture'


def test_invalid_previous_report_cannot_be_silently_replaced(tree):
    path = tree / '.datacore/state/agents/fixture.json'
    path.parent.mkdir(parents=True)
    path.write_text('{invalid')
    with pytest.raises(ValueError):
        report.write_self_report('fixture', 'Fixture')
    assert path.read_text() == '{invalid'


def test_unreadable_report_is_not_absence(tree):
    path = tree / '.datacore/state/agents/fixture.json'
    path.parent.mkdir(parents=True)
    path.write_text('{invalid')
    with pytest.raises(ValueError):
        report.read_self_report('fixture')


def test_report_publication_failure_is_explicit(tree, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('simulated write failure')
    # New implementation uses the shared durable primitive; this fallback
    # reproduces the old silent failure at its actual Path replacement call.
    if hasattr(report, 'atomic_write_text_within'):
        monkeypatch.setattr(report, 'atomic_write_text_within', fail)
    else:
        monkeypatch.setattr(type(tree), 'replace', fail)
    with pytest.raises(OSError):
        report.write_self_report('fixture', 'Fixture')


def test_report_contains_declared_writer_and_readback(tree):
    path = report.write_self_report('fixture', 'Fixture', 'blocked', error='policy-hold', summary='No task executed')
    saved = json.loads(path.read_text())
    assert saved['writer'] == 'fixture'
    assert saved['last_status'] == 'blocked'
    assert report.read_self_report('fixture') == saved


@pytest.mark.parametrize('extra', [{'writer': 'other'}, {'values': {1: 'ambiguous'}},
                                  {'value': float('nan')}, {'value': float('inf')}])
def test_extensions_cannot_override_identity_or_introduce_ambiguous_values(tree, extra):
    with pytest.raises(ValueError):
        report.write_self_report('fixture', 'Fixture', extra=extra)


def test_regressing_clock_cannot_overwrite_newer_status(tree, monkeypatch):
    current = datetime.now(timezone.utc) - timedelta(minutes=1)
    monkeypatch.setattr(report, '_now_iso', lambda: current.isoformat())
    path = report.write_self_report('fixture', 'Fixture', 'error', error='incomplete')
    original = path.read_bytes()
    monkeypatch.setattr(report, '_now_iso', lambda: (current - timedelta(seconds=1)).isoformat())
    with pytest.raises(ValueError):
        report.write_self_report('fixture', 'Fixture', 'ok')
    assert path.read_bytes() == original


def test_same_instant_with_a_different_offset_cannot_resolve_a_conflict(tree, monkeypatch):
    current = datetime.now(timezone.utc) - timedelta(minutes=1)
    monkeypatch.setattr(report, '_now_iso', lambda: current.isoformat())
    path = report.write_self_report('fixture', 'Fixture', 'error', error='incomplete')
    original = path.read_bytes()
    monkeypatch.setattr(report, '_now_iso', lambda: current.astimezone(timezone(timedelta(hours=1))).isoformat())
    with pytest.raises(ValueError):
        report.write_self_report('fixture', 'Fixture', 'ok')
    assert path.read_bytes() == original


def test_report_source_directory_cannot_redirect_a_bounded_read(tree, tmp_path):
    state = tree / '.datacore/state'
    state.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (state / 'agents').symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        report.write_self_report('fixture', 'Fixture')
    assert not list(outside.iterdir())


def test_valid_legacy_extensions_survive_an_attributed_update(tree):
    path = report.write_self_report('fixture', 'Fixture', extra={'counter': 7})
    previous = json.loads(path.read_text())
    previous.pop('writer')
    path.write_text(json.dumps(previous))
    report.write_self_report('fixture', 'Fixture', 'blocked', error='policy')
    saved = report.read_self_report('fixture')
    assert saved['counter'] == 7 and saved['writer'] == 'fixture'
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('source', [
    '{"name":"Fixture","last_status":"error","last_status":"ok"}',
    '{"extension":1e999,"name":"Fixture","last_status":"ok","last_error":null,"last_summary":"","last_activity":"2026-01-01T00:00:00Z"}',
])
def test_duplicate_or_overflowing_json_cannot_become_valid_evidence(tree, source):
    path = tree / '.datacore/state/agents/fixture.json'
    path.parent.mkdir(parents=True)
    path.write_text(source)
    with pytest.raises(ValueError):
        report.read_self_report('fixture')
    assert path.read_text() == source


def test_missing_actor_declaration_cannot_fall_back_to_a_hostname(tree, monkeypatch):
    monkeypatch.setattr(report, 'this_actor', lambda **kwargs: None)
    with pytest.raises(ValueError):
        report.write_self_report('fixture', 'Fixture')
