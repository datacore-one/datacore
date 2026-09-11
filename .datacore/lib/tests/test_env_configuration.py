"""Malformed configuration must not leak values or partially select credentials."""
import os

import pytest
from env_utils import parse_env_file, load_env_files


@pytest.mark.parametrize('content', [
    'TOKEN=first\nTOKEN=second\n', 'INVALID KEY=synthetic-secret\n',
    'BROKEN LINE synthetic-secret\n', 'KEY="unterminated synthetic-secret\n',
    'KEY=synthetic\0secret\n',
])
def test_ambiguous_environment_is_refused_without_echoing_values(tmp_path, content):
    path = tmp_path / 'env'
    path.write_text(content)
    with pytest.raises(ValueError) as failure:
        parse_env_file(path)
    assert 'synthetic' not in str(failure.value)


def test_missing_optional_environment_differs_from_broken_link(tmp_path):
    assert parse_env_file(tmp_path / 'absent') == {}
    link = tmp_path / 'broken'
    link.symlink_to(tmp_path / 'absent')
    with pytest.raises(ValueError):
        parse_env_file(link)


@pytest.mark.parametrize('inline_comments,expected', [(False, 'literal#value # comment'), (True, 'literal#value')])
def test_unquoted_comment_policy_is_explicit(tmp_path, inline_comments, expected):
    path = tmp_path / 'env'
    path.write_text('KEY=literal#value # comment\n')
    assert parse_env_file(path, inline_comments=inline_comments) == {'KEY': expected}


def test_later_invalid_file_cannot_partially_change_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_KEY', 'original')
    monkeypatch.delenv('FIXTURE_NEW', raising=False)
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.write_text('FIXTURE_KEY=changed\nFIXTURE_NEW=new\n')
    second.write_text('invalid fixture configuration\n')
    with pytest.raises(ValueError):
        load_env_files([first, second], override=True)
    assert os.environ['FIXTURE_KEY'] == 'original'
    assert 'FIXTURE_NEW' not in os.environ


@pytest.mark.parametrize('override,expected', [(False, 'first'), (True, 'second')])
def test_valid_layer_precedence_is_preserved(tmp_path, monkeypatch, override, expected):
    monkeypatch.delenv('FIXTURE_KEY', raising=False)
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.write_text('FIXTURE_KEY=first\n')
    second.write_text('FIXTURE_KEY=second\n')
    load_env_files([first, second], override=override)
    try:
        assert os.environ['FIXTURE_KEY'] == expected
    finally:
        os.environ.pop('FIXTURE_KEY', None)


@pytest.mark.parametrize('value', [
    '$HOME', '$(printf INJECTION)', '`printf INJECTION`',
    "quote' dollar$ and back`tick", r'slash\$ and slash\`',
])
def test_shell_and_systemd_escaped_values_are_literal(tmp_path, value):
    import re
    # POSIX double quotes and systemd EnvironmentFile use these four escapes.
    encoded = '"' + re.sub(r'([\\"$`])', r'\\\1', value) + '"'
    path = tmp_path / 'runtime.env'
    path.write_text('FIXTURE=' + encoded + '\n')
    assert parse_env_file(path) == {'FIXTURE': value}
