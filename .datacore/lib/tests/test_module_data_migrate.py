import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import module_data_migrate as migration


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / 'install'
    config = root / 'named/.datacore/config.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('space: {name: self, type: personal}\n')
    code = tmp_path / 'provider'
    (code / 'data/empty').mkdir(parents=True)
    (code / 'state').mkdir()
    (code / 'data/note.bin').write_bytes(b'preserve\x00\xff\r\n')
    (code / 'state/run.json').write_text('{"run":1}')
    (code / 'settings.local.yaml').write_text('setting: retained\n')
    (code / 'module.yaml').write_text('name: fixture\n')
    db = sqlite3.connect(code / 'data/records.db')
    db.execute('create table records (value text)')
    db.execute('insert into records values (?)', ('preserved',))
    db.commit()
    db.close()
    target = root / 'named/.datacore/module-data/fixture'
    return root, code, target


def run(layout):
    root, code, _ = layout
    return migration.migrate(root, 'self', 'fixture', code, quiesced=True)


def test_preserves_binary_sqlite_empty_directories_settings_and_original_backup(layout):
    root, code, target = layout
    original = migration._snapshot(code, list(migration.COMPONENTS))
    assert run(layout) == {'status': 'complete', 'retry': False, 'components': 3}
    assert migration._snapshot(target, list(migration.COMPONENTS)) == original
    receipt = json.loads((target / '.migration.json').read_text())
    assert migration._snapshot(Path(receipt['backup']), list(migration.COMPONENTS)) == original
    assert (code / 'module.yaml').read_text() == 'name: fixture\n'
    assert not any((code / c).exists() for c in migration.COMPONENTS)
    assert target.stat().st_mode & 0o077 == 0
    assert (target / 'data/note.bin').stat().st_mode & 0o077 == 0
    with sqlite3.connect(target / 'data/records.db') as db:
        assert db.execute('select value from records').fetchone() == ('preserved',)
        assert db.execute('pragma integrity_check').fetchone() == ('ok',)


def test_retry_never_replaces_valid_post_cutover_writes(layout):
    run(layout)
    target = layout[2]
    (target / 'data/note.bin').write_bytes(b'new acknowledged data')
    assert run(layout)['retry'] is True
    assert (target / 'data/note.bin').read_bytes() == b'new acknowledged data'


def test_short_receipt_and_source_reads_support_preserved_retry(layout, monkeypatch):
    original = os.read
    monkeypatch.setattr(migration.os, 'read', lambda fd, size: original(fd, min(size, 11)))
    assert run(layout)['status'] == 'complete'
    assert run(layout)['retry'] is True


def test_unreadable_receipt_size_cannot_retire_originals(layout, monkeypatch):
    monkeypatch.setattr(migration, 'MAX_RECEIPT_BYTES', 100)
    original = migration._snapshot(layout[1], list(migration.COMPONENTS))
    with pytest.raises(ValueError, match='receipt exceeds limit'):
        run(layout)
    assert not layout[2].exists()
    assert migration._snapshot(layout[1], list(migration.COMPONENTS)) == original


def test_requires_quiescence_and_unambiguous_scope(layout):
    root, code, target = layout
    with pytest.raises(ValueError, match='quiesced'):
        migration.migrate(root, 'self', 'fixture', code)
    with pytest.raises(ValueError, match='ambiguous'):
        migration.migrate(root, 'missing', 'fixture', code, quiesced=True)
    assert not target.exists()
    assert (code / 'data/note.bin').exists()


def test_existing_destination_is_not_overwritten(layout):
    _, code, target = layout
    target.mkdir(parents=True, mode=0o700)
    target.parent.chmod(0o700)
    (target / 'valuable.txt').write_text('existing')
    with pytest.raises(ValueError, match='destination'):
        run(layout)
    assert (target / 'valuable.txt').read_text() == 'existing'
    assert (code / 'data/note.bin').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_refuses_unsafe_legacy_entries_without_changing_originals(layout, kind):
    _, code, target = layout
    extra = code / 'data/unsafe'
    if kind == 'symlink':
        extra.symlink_to(code / 'module.yaml')
    elif kind == 'hardlink':
        os.link(code / 'module.yaml', extra)
    else:
        os.mkfifo(extra)
    with pytest.raises(ValueError):
        run(layout)
    assert not target.exists()
    assert (code / 'data/note.bin').exists()


def test_copy_failure_preserves_source_and_never_publishes_partial_destination(layout, monkeypatch):
    _, code, target = layout
    original = migration._snapshot(code, list(migration.COMPONENTS))
    def fail(*args):
        raise OSError('injected copy failure')
    monkeypatch.setattr(migration, '_copy', fail)
    with pytest.raises(OSError):
        run(layout)
    assert not target.exists()
    assert migration._snapshot(code, list(migration.COMPONENTS)) == original


def test_partial_retirement_is_recoverable_without_overwriting_either_copy(layout, monkeypatch):
    _, code, target = layout
    rename = os.rename
    def interrupt(source, destination):
        if Path(source) == code / 'state':
            raise OSError('injected interruption after data retirement')
        return rename(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(migration.os, 'rename', interrupt)
        with pytest.raises(OSError):
            run(layout)
    receipt = json.loads((target / '.migration.json').read_text())
    assert receipt['status'] == 'staged'
    assert not (code / 'data').exists()
    assert (code / 'state/run.json').exists()
    assert (Path(receipt['backup']) / 'data/note.bin').read_bytes() == b'preserve\x00\xff\r\n'
    assert run(layout)['status'] == 'complete'


def test_changed_source_after_staging_does_not_complete_or_destroy_new_data(layout, monkeypatch):
    _, code, target = layout
    rename = os.rename
    def change(source, destination):
        if Path(source) == code / 'state':
            (code / 'state/run.json').write_text('{"run":2}')
        return rename(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(migration.os, 'rename', change)
        with pytest.raises(ValueError, match='differs'):
            run(layout)
    receipt = json.loads((target / '.migration.json').read_text())
    assert receipt['status'] == 'staged'
    assert (Path(receipt['backup']) / 'state/run.json').read_text() == '{"run":2}'
    assert (target / 'state/run.json').read_text() == '{"run":1}'
    with pytest.raises(ValueError):
        run(layout)


def test_completion_receipt_failure_remains_recoverable(layout, monkeypatch):
    write = migration.atomic_write_json
    def fail(path, value):
        if value.get('status') == 'complete':
            raise OSError('injected receipt failure')
        return write(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'atomic_write_json', fail)
        with pytest.raises(OSError):
            run(layout)
    assert json.loads((layout[2] / '.migration.json').read_text())['status'] == 'staged'
    assert run(layout)['status'] == 'complete'


def test_forged_receipt_components_cannot_escape_source(layout, monkeypatch):
    write = migration.atomic_write_json
    def stop(path, value):
        if value.get('status') == 'complete':
            raise OSError('stop before completion')
        return write(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'atomic_write_json', stop)
        with pytest.raises(OSError):
            run(layout)
    path = layout[2] / '.migration.json'
    receipt = json.loads(path.read_text())
    receipt['components'] = ['../module.yaml']
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        run(layout)
    assert (layout[1] / 'module.yaml').read_text() == 'name: fixture\n'


def test_real_competing_process_cannot_enter_migration(layout):
    root, code, target = layout
    parent = migration._private(target.parent, root / 'named')
    with migration._lock(parent, 'fixture'):
        result = subprocess.run([sys.executable, '-I', migration.__file__, '--root', str(root),
                                 '--space', 'self', '--module', 'fixture', '--source', str(code), '--quiesced'],
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout)['status'] == 'incomplete'
    assert not target.exists()
    assert (code / 'data/note.bin').exists()
    assert run(layout)['status'] == 'complete'


def test_cross_device_retirement_retains_both_copies_for_reconciliation(layout, monkeypatch):
    import errno
    _, code, target = layout
    rename = os.rename
    def different_device(source, destination):
        if Path(source) == code / 'data':
            raise OSError(errno.EXDEV, 'injected cross-device rename')
        return rename(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(migration.os, 'rename', different_device)
        with pytest.raises(OSError):
            run(layout)
    assert (code / 'data/note.bin').read_bytes() == (target / 'data/note.bin').read_bytes()
    assert json.loads((target / '.migration.json').read_text())['status'] == 'staged'
    assert run(layout)['status'] == 'complete'


def test_retry_must_flush_published_destination_before_retiring_any_original(layout, monkeypatch):
    _, code, target = layout
    flush = migration.fsync_directory
    def fail_publication(directory):
        if Path(directory) == target.parent and target.exists():
            raise OSError('injected destination directory flush failure')
        return flush(directory)
    with monkeypatch.context() as patch:
        patch.setattr(migration, 'fsync_directory', fail_publication)
        for _ in range(2):
            with pytest.raises(OSError):
                run(layout)
            assert (code / 'data/note.bin').exists()
            assert (code / 'state/run.json').exists()
    assert run(layout)['status'] == 'complete'
