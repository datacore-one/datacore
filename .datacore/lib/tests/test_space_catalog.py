import json
from pathlib import Path
import subprocess
import sys

import pytest

from space_catalog import catalog


def marker(root, relative, name, kind='team'):
    p = root / relative / '.datacore/config.yaml'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'space:\n  name: {name}\n  type: {kind}\n')
    return p


def test_canonical_root_nested_named_and_legacy_union(tmp_path):
    marker(tmp_path, '.', 'root', 'personal')
    marker(tmp_path, 'named/group/client', 'client', 'client')
    marker(tmp_path, '12-local-ordinal', 'stable')
    (tmp_path / '23-legacy/org').mkdir(parents=True)
    (tmp_path / '4-stray').mkdir()
    (tmp_path / '5-old-archive/org').mkdir(parents=True)
    assert catalog(tmp_path) == {'version': 1, 'spaces': [
        {'path': '.', 'name': 'root', 'type': 'personal', 'marked': True},
        {'path': '12-local-ordinal', 'name': 'stable', 'type': 'team', 'marked': True},
        {'path': '23-legacy', 'name': 'legacy', 'type': 'unknown', 'marked': False},
        {'path': 'named/group/client', 'name': 'client', 'type': 'client', 'marked': True},
    ]}


def test_identity_survives_ordinal_rename(tmp_path):
    marker(tmp_path, '1-local', 'stable')
    before = catalog(tmp_path)['spaces'][0]
    (tmp_path / '1-local').rename(tmp_path / '22-local')
    after = catalog(tmp_path)['spaces'][0]
    assert before['name'] == after['name'] == 'stable'
    assert after['path'] == '22-local'


def test_installed_module_code_is_not_a_space_or_external_space_alias(tmp_path):
    root = tmp_path / 'install'
    marker(root, 'named', 'space')
    provider = tmp_path / 'installed-provider'
    (provider / '.git').mkdir(parents=True)
    modules = root / '.datacore/modules'
    modules.mkdir(parents=True)
    (modules / 'provider').symlink_to(provider, target_is_directory=True)
    marker(root, '.datacore/modules/bundled-fixture', 'not-a-data-space')
    assert [s['name'] for s in catalog(root)['spaces']] == ['space']


def test_duplicate_identity_refuses_instead_of_selecting_first(tmp_path):
    marker(tmp_path, 'a', 'same')
    marker(tmp_path, 'b', 'same')
    with pytest.raises(ValueError, match='ambiguous'):
        catalog(tmp_path)


def test_external_alias_refuses_and_internal_alias_is_not_duplicate(tmp_path):
    root = tmp_path / 'install'
    root.mkdir()
    marker(root, 'named', 'stable')
    (root / '1-alias').symlink_to(root / 'named', target_is_directory=True)
    assert len(catalog(root)['spaces']) == 1
    marker(tmp_path, 'outside', 'outside')
    (root / '2-escape').symlink_to(tmp_path / 'outside', target_is_directory=True)
    with pytest.raises(ValueError):
        catalog(root)


@pytest.mark.parametrize('contents', ['space: [bad]', 'space:\n  name: one\n  name: two',
                                     'space: {name: ""}', 'space: {name: [private-value]}'])
def test_malformed_catalog_is_not_partial_success(tmp_path, contents):
    marker(tmp_path, 'valid', 'valid')
    bad = marker(tmp_path, 'bad', 'bad')
    bad.write_text(contents)
    command = [sys.executable, '-I', str(Path(__file__).parents[1] / 'space_catalog.py'), '--root', str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {'version': 1, 'error': 'discovery-unverified'}
    assert result.stderr == ''


def test_installed_isolated_cli_returns_no_configuration_secrets(tmp_path):
    p = marker(tmp_path, '.', 'private', 'personal')
    p.write_text(p.read_text() + '  owner: synthetic-owner\nsecret: synthetic-credential\n')
    result = subprocess.run([sys.executable, '-I', str(Path(__file__).parents[1] / 'space_catalog.py'),
                             '--root', str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout) == catalog(tmp_path)
    assert 'synthetic' not in result.stdout + result.stderr
    assert not (p.parent / '__pycache__').exists()
