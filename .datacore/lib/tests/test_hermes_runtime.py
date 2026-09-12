"""Authenticated source preparation must preserve paths and concurrent work."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest
from hermes_runtime import prepare as runtime


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
