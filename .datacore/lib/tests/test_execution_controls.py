"""Operator controls fail closed without leaking the invalid document."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from execution_controls import PolicyError, globally_paused, read_policy


@pytest.mark.parametrize('value', [
    b'', b'[]', b'null', b'\xff', b'key: [', b'enabled: "false"',
    b'paused_cadences: null', b'paused_cadences: [a, 1]', b'paused_cadences: [" "]',
    b'snoozed_until: nope', b'snoozed_until: true', b'enabled: false\nenabled: true',
    b'1: true', b'a: &a {key: value}\nb: {<<: *a}', b' ' * 65537,
])
def test_invalid_documents_are_not_defaults(tmp_path, value):
    path = tmp_path / 'controls.yaml'
    path.write_bytes(value)
    with pytest.raises(PolicyError):
        read_policy(path)


def test_only_missing_optional_policy_is_default(tmp_path, monkeypatch):
    path = tmp_path / 'controls.yaml'
    assert read_policy(path) == {}
    path.symlink_to(tmp_path / 'missing')
    with pytest.raises(PolicyError):
        read_policy(path)
    def denied(*a, **kw):
        raise PermissionError('fixture sensitive detail')
    path.unlink()
    path.write_text('enabled: false')
    monkeypatch.setattr('execution_controls.read_text_within', denied)
    with pytest.raises(PolicyError, match='unreadable') as failure:
        read_policy(path)
    assert 'sensitive' not in str(failure.value)


@pytest.mark.parametrize('stamp', [
    '2030-01-01T12:00:00Z', '2030-01-01T12:00:00', '2030-01-01T14:00:00+02:00',
])
def test_yaml_and_quoted_timestamps_have_the_same_snooze(stamp, tmp_path):
    for value in (stamp, '"' + stamp + '"'):
        path = tmp_path / 'controls.yaml'
        path.write_text('snoozed_until: ' + value)
        policy = read_policy(path)
        assert globally_paused(policy, datetime(2030, 1, 1, 11, tzinfo=timezone.utc))[0]
        assert not globally_paused(policy, datetime(2030, 1, 1, 12, tzinfo=timezone.utc))[0]


def test_disabled_control_and_valid_empty_controls(tmp_path):
    path = tmp_path / 'controls.yaml'
    path.write_text('enabled: false\npaused_cadences: []\nsnoozed_until: null')
    assert globally_paused(read_policy(path)) == (True, None)
    path.write_text('{}')
    assert globally_paused(read_policy(path)) == (False, None)
