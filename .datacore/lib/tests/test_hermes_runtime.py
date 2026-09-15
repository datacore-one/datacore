"""Authenticated source preparation must preserve paths and concurrent work."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest
from hermes_runtime import prepare as runtime
from hermes_runtime import verify_environment as verification


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as target:
        for name, kind, content in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(content) if kind == tarfile.REGTYPE else 0
            if kind == tarfile.SYMTYPE:
                member.linkname = '/outside'
            target.addfile(member, io.BytesIO(content) if member.size else None)
    return output.getvalue()


@pytest.fixture()
def recipe(tmp_path):
    kit = tmp_path / 'kit'
    kit.mkdir()
    data = archive([('source/example.txt', tarfile.REGTYPE, b'original\n')])
    source = tmp_path / 'source.tar.gz'
    source.write_bytes(data)
    patch = b'--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-original\n+patched\n'
    lock = b'version = 1\n'
    (kit / 'source.patch').write_bytes(patch)
    (kit / 'uv.lock').write_bytes(lock)
    manifest = {
        'format_version': 1, 'archive_root': 'source',
        'source_sha256': hashlib.sha256(data).hexdigest(),
        'patch_sha256': hashlib.sha256(patch).hexdigest(),
        'lock_sha256': hashlib.sha256(lock).hexdigest(),
        'requirements_sha256': {},
        'patched_files_sha256': {'example.txt': hashlib.sha256(b'patched\n').hexdigest()},
    }
    (kit / 'manifest.json').write_text(json.dumps(manifest))
    return source, kit


def test_preparation_applies_verified_patch_and_lock_without_building(tmp_path, recipe):
    source, kit = recipe
    target = tmp_path / 'prepared'
    assert runtime.prepare(source, target, kit=kit) == target
    assert (target / 'example.txt').read_bytes() == b'patched\n'
    assert (target / 'uv.lock').read_bytes() == (kit / 'uv.lock').read_bytes()


@pytest.mark.parametrize('input_name', ['archive', 'source.patch', 'uv.lock'])
def test_tampered_inputs_cannot_publish_source(tmp_path, recipe, input_name):
    source, kit = recipe
    changed = source if input_name == 'archive' else kit / input_name
    changed.write_bytes(changed.read_bytes() + b'tampered')
    target = tmp_path / 'prepared'
    with pytest.raises(ValueError, match='checksum'):
        runtime.prepare(source, target, kit=kit)
    assert not target.exists()


def test_existing_destination_is_preserved(tmp_path, recipe):
    source, kit = recipe
    target = tmp_path / 'prepared'
    target.mkdir()
    (target / 'work').write_text('existing work')
    with pytest.raises(ValueError, match='exists'):
        runtime.prepare(source, target, kit=kit)
    assert (target / 'work').read_text() == 'existing work'


def test_concurrent_empty_directory_cannot_be_replaced(tmp_path, recipe, monkeypatch):
    source, kit = recipe
    target = tmp_path / 'prepared'
    publish = runtime.rename_directory_noreplace
    expected = {}
    def race(tree, destination):
        destination.mkdir(mode=0o700)
        expected['inode'] = destination.stat().st_ino
        return publish(tree, destination)
    monkeypatch.setattr(runtime, 'rename_directory_noreplace', race)
    with pytest.raises(FileExistsError):
        runtime.prepare(source, target, kit=kit)
    assert target.stat().st_ino == expected['inode']
    assert list(target.iterdir()) == []
    assert not list(tmp_path.glob('.hermes-prepare-*'))


@pytest.mark.parametrize('entries', [
    [('source/../../outside', tarfile.REGTYPE, b'escaped')],
    [('/outside', tarfile.REGTYPE, b'escaped')],
    [('other/file', tarfile.REGTYPE, b'wrong root')],
    [('source/link', tarfile.SYMTYPE, b'')],
    [('source/pipe', tarfile.FIFOTYPE, b'')],
    [('source/file', tarfile.REGTYPE, b'one'), ('source/file', tarfile.REGTYPE, b'two')],
])
def test_unsafe_archive_is_rejected_before_any_member_write(tmp_path, entries):
    target = tmp_path / 'extracted'
    target.mkdir()
    with pytest.raises(ValueError):
        runtime._extract(archive(entries), 'source', target)
    assert list(target.iterdir()) == []
    assert not (tmp_path / 'outside').exists()


def test_preparation_accepts_relative_command_line_paths(tmp_path, recipe, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = Path('prepared')
    assert runtime.prepare(Path('source.tar.gz'), target, kit=Path('kit')) == target
    assert (target / 'example.txt').read_bytes() == b'patched\n'


def verification_kit(tmp_path, requirements):
    profile = 'datacore-telegram'
    raw = requirements.encode()
    (tmp_path / (profile + '.requirements.txt')).write_bytes(raw)
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'format_version': 1, 'version': '0.19.0+datacore.1',
        'requirements_sha256': {profile: hashlib.sha256(raw).hexdigest()},
    }))
    return profile


def test_runtime_lock_uses_platform_markers_and_normalized_names(tmp_path):
    profile = verification_kit(tmp_path,
        'Example_Package==1.2.3 \\\n    --hash=sha256:' + 'a' * 64 + '\n'
        'example-package==9.0; python_version < "2" --hash=sha256:' + 'b' * 64 + '\n')
    expected = verification.expected_packages(profile, kit=tmp_path)
    assert expected == {'hermes-agent': '0.19.0+datacore.1', 'example-package': '1.2.3'}
    result = verification.compare_packages(expected, [('hermes_agent', '0.19.0+datacore.1'),
                                                       ('Example_Package', '1.2.3')])
    assert result['status'] == 'PASS'


@pytest.mark.parametrize('requirement', [
    'example>=1.0', 'example==1.*', 'example @ https://invalid.example/package.whl',
])
def test_runtime_verifier_refuses_unpinned_inputs(tmp_path, requirement):
    profile = verification_kit(tmp_path, requirement + ' --hash=sha256:' + 'a' * 64 + '\n')
    with pytest.raises(ValueError):
        verification.expected_packages(profile, kit=tmp_path)


def test_runtime_verifier_refuses_missing_hash_and_active_duplicates(tmp_path):
    profile = verification_kit(tmp_path, 'example==1.2.3\n')
    with pytest.raises(ValueError, match='hash'):
        verification.expected_packages(profile, kit=tmp_path)
    profile = verification_kit(tmp_path, ('example==1.2.3 --hash=sha256:' + 'a' * 64 + '\n') * 2)
    with pytest.raises(ValueError, match='duplicate'):
        verification.expected_packages(profile, kit=tmp_path)


def test_runtime_verifier_rejects_changed_lock_and_profile_paths(tmp_path):
    profile = verification_kit(tmp_path, 'example==1.2.3 --hash=sha256:' + 'a' * 64 + '\n')
    (tmp_path / (profile + '.requirements.txt')).write_text('example==9.0\n')
    with pytest.raises(ValueError, match='checksum'):
        verification.expected_packages(profile, kit=tmp_path)
    with pytest.raises(ValueError, match='profile'):
        verification.expected_packages('../other', kit=tmp_path)


def test_runtime_verifier_detects_missing_extra_stale_and_duplicate_distributions():
    result = verification.compare_packages({'one': '1', 'two': '2'},
                                           [('one', '0'), ('ONE', '0'), ('three', '3')])
    assert result == {'status': 'DRIFT', 'packages': 2, 'missing': ['two'],
                      'extra': ['three'], 'version_mismatch': ['one'],
                      'duplicate_distributions': ['one']}


def test_shipped_runtime_profiles_are_hashed_and_exactly_pinned():
    for profile in verification.PROFILES:
        expected = verification.expected_packages(profile)
        assert expected['hermes-agent'] == '0.19.0+datacore.1'
        assert expected['cryptography'] == '50.0.0'
        assert expected['python-telegram-bot'] == '22.6'
        assert ('elevenlabs' in expected) == profile.endswith('-tts')


@pytest.mark.parametrize('manifest', [[], {'format_version': True}])
def test_runtime_verifier_refuses_invalid_manifest_types(tmp_path, manifest):
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='manifest'):
        verification.expected_packages('datacore-telegram', kit=tmp_path)
