"""A declared optional dependency may be mandatory for the deployed feature set."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'node_runtime/verify_environment.py'
spec = importlib.util.spec_from_file_location('node_profile_verifier', PATH)
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def package(root, name='fixture', version='1.0.0'):
    directory = root / 'node_modules' / name
    directory.mkdir(parents=True)
    (directory / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
    return directory


def lock(name='fixture', **entry):
    return {'lockfileVersion': 3, 'packages': {'': {}, 'node_modules/' + name:
            {'version': '1.0.0', **entry}}}


def test_locked_inventory_and_optional_platform_packages(tmp_path):
    package(tmp_path)
    value = lock()
    value['packages']['node_modules/another-platform'] = {'version': '2.0.0', 'optional': True}
    assert V.verify_packages(tmp_path, value, ['fixture']) == {'packages': 1, 'missing_optional': 1}


def test_selected_feature_cannot_disappear_as_optional(tmp_path):
    with pytest.raises(ValueError, match='required package'):
        V.verify_packages(tmp_path, lock(optional=True), ['fixture'])


def test_required_feature_must_be_declared(tmp_path):
    package(tmp_path)
    with pytest.raises(ValueError, match='runtime feature'):
        V.verify_packages(tmp_path, lock(), ['native-inference'])


@pytest.mark.parametrize('changes', [{'version': '0.9.0'}, {'name': 'impersonator'}])
def test_manifest_identity_drift_refused(tmp_path, changes):
    p = package(tmp_path) / 'package.json'
    value = json.loads(p.read_text());value.update(changes);p.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='differs from the lock'):
        V.verify_packages(tmp_path, lock(), ['fixture'])


def test_unlocked_dependency_refused(tmp_path):
    package(tmp_path);package(tmp_path, 'unexpected')
    with pytest.raises(ValueError, match='unlocked'):
        V.verify_packages(tmp_path, lock(), ['fixture'])


def test_scoped_and_nested_packages_are_checked(tmp_path):
    parent = package(tmp_path, '@scope/parent')
    package(parent, 'child')
    value = lock('@scope/parent')
    value['packages']['node_modules/@scope/parent/node_modules/child'] = {'version': '1.0.0'}
    assert V.verify_packages(tmp_path, value, ['@scope/parent', 'child'])['packages'] == 2


@pytest.mark.parametrize('relative', ['../outside', '/node_modules/a',
                                   'node_modules/../outside', 'node_modules//a',
                                   'node_modules/a\\b'])
def test_lock_paths_cannot_escape_or_alias(tmp_path, relative):
    with pytest.raises(ValueError):
        V.verify_packages(tmp_path, {'lockfileVersion': 3, 'packages':
                                    {relative: {'version': '1.0.0'}}}, [])


def test_package_directory_alias_refused_even_if_metadata_matches(tmp_path):
    outside = tmp_path / 'outside';package(outside)
    (tmp_path / 'node_modules').symlink_to(outside / 'node_modules', target_is_directory=True)
    with pytest.raises(ValueError, match='alias'):
        V.verify_packages(tmp_path, lock(), ['fixture'])


def test_manifest_alias_refused(tmp_path):
    p = package(tmp_path) / 'package.json'
    backup = p.with_suffix('.backup');p.rename(backup);p.symlink_to(backup)
    with pytest.raises(OSError):
        V.verify_packages(tmp_path, lock(), ['fixture'])


def test_ambiguous_metadata_refused(tmp_path):
    p = package(tmp_path) / 'package.json'
    p.write_text('{"name":"fixture","version":"0.9.0","version":"1.0.0"}')
    with pytest.raises(ValueError, match='duplicate'):
        V.verify_packages(tmp_path, lock(), ['fixture'])


def test_binary_digest_uses_bytes_not_reported_version(tmp_path):
    node = tmp_path / 'node';node.write_bytes(b'fixture binary')
    assert V._binary_digest(node) == hashlib.sha256(node.read_bytes()).hexdigest()
    before = V._binary_digest(node);node.write_bytes(b'changed binary')
    assert V._binary_digest(node) != before


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX pipe check')
def test_binary_fifo_refused_without_blocking(tmp_path):
    node = tmp_path / 'node';os.mkfifo(node)
    with pytest.raises(ValueError, match='invalid Node'):
        V._binary_digest(node)


def test_binary_symlink_refused(tmp_path):
    node = tmp_path / 'node';node.write_bytes(b'node')
    alias = tmp_path / 'alias';alias.symlink_to(node)
    with pytest.raises(OSError):
        V._binary_digest(alias)


def test_oversized_binary_refused_before_reading(tmp_path):
    node = tmp_path / 'node'
    with node.open('wb') as stream:stream.truncate(512 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='invalid Node'):
        V._binary_digest(node)


def installation(tmp_path):
    root = tmp_path / 'installation';root.mkdir()
    kit = tmp_path / 'kit';kit.mkdir()
    package(root)
    pairs = {'package.json': 'plur.package.json', 'package-lock.json': 'plur.package-lock.json',
             'verify-plur.mjs': 'verify-plur.mjs', 'deny-archive.cjs': 'deny-archive.cjs'}
    data = {'package.json': '{}', 'package-lock.json': json.dumps(lock()),
            'verify-plur.mjs': '// check\n', 'deny-archive.cjs': '// instrument\n'}
    files = {}
    for target, source in pairs.items():
        content = data[target].encode();(root / target).write_bytes(content)
        (kit / source).write_bytes(content);files[source] = hashlib.sha256(content).hexdigest()
    node = tmp_path / 'node';node.write_bytes(b'fixture executable')
    profile = {'version': 1, 'files': files, 'required_packages': ['fixture'],
               'node': {'version': 'fixture', 'linux_x64_binary_sha256': V._binary_digest(node)}}
    (kit / 'manifest.json').write_text(json.dumps(profile))
    return root, node, kit


def test_full_profile_identity_verification(tmp_path):
    root, node, kit = installation(tmp_path)
    assert V.verify(root, node, kit)['status'] == 'PASS'


@pytest.mark.parametrize('name', ['package.json', 'package-lock.json',
                                 'verify-plur.mjs', 'deny-archive.cjs'])
def test_modified_deployment_inputs_cannot_claim_profile_match(tmp_path, name):
    root, node, kit = installation(tmp_path)
    with (root / name).open('ab') as f:f.write(b'\n')
    with pytest.raises(ValueError, match='inputs differ'):
        V.verify(root, node, kit)


def test_another_node_binary_cannot_claim_supported_version(tmp_path):
    root, node, kit = installation(tmp_path);node.write_bytes(b'another executable')
    with pytest.raises(ValueError, match='Node binary differs'):
        V.verify(root, node, kit)
