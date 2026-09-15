"""Journal mutations participate in core serialization and crash recovery."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_utils
import journal_store
import org_transaction


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))


def test_fifo_cannot_block_a_journal_reader(tmp_path):
    path = tmp_path / 'journal.md'
    os.mkfifo(path)
    with pytest.raises(ValueError, match='regular file'):
        journal_store.read_journal(path)


def test_invalid_utf8_never_becomes_an_empty_journal(tmp_path):
    path = tmp_path / 'journal.md'
    original = b'Authored note.\xff\n'
    path.write_bytes(original)
    with pytest.raises(UnicodeDecodeError):
        journal_store.update_journal(path, lambda _: 'Replacement.')
    assert path.read_bytes() == original


def test_journal_updates_join_outer_transaction_rollback(tmp_path):
    paths = [tmp_path / 'first.md', tmp_path / 'second.md']
    for path in paths:
        path.write_text('Original.\n')

    @org_transaction.serialized
    def operation():
        for path in paths:
            journal_store.update_journal(path, lambda text: text + 'Pending.\n')
        raise RuntimeError('injected later failure')
    with pytest.raises(RuntimeError, match='later failure'):
        operation()
    assert all(path.read_text() == 'Original.\n' for path in paths)
    assert not org_transaction.journal_path().exists()


def _crash_after_write(path):
    code = '''from pathlib import Path
import os, sys, journal_store, org_transaction
org_transaction.Transaction.commit = lambda self: os._exit(77)
journal_store.update_journal(Path(sys.argv[1]), lambda text: text + 'Unacknowledged.\\n')
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(journal_store.__file__).parent))
    child = subprocess.run([sys.executable, '-c', code, str(path)], env=environment,
                           capture_output=True, timeout=10)
    assert child.returncode == 77, (child.stdout, child.stderr)
    assert 'Unacknowledged.' in path.read_text()
    assert org_transaction.journal_path().exists()


def test_restart_recovers_unacknowledged_write_before_appending(tmp_path):
    path = tmp_path / 'journal.md'
    path.write_text('Original.\n')
    _crash_after_write(path)
    journal_store.update_journal(path, lambda text: text + 'Acknowledged.\n')
    assert path.read_text() == 'Original.\nAcknowledged.\n'
    assert not org_transaction.journal_path().exists()


def test_recovery_cannot_erase_external_changes_after_crash(tmp_path):
    path = tmp_path / 'journal.md'
    path.write_text('Original.\n')
    _crash_after_write(path)
    latest = path.read_text() + 'Other writer.\n'
    path.write_text(latest)
    with pytest.raises(org_transaction.RecoveryRequired):
        journal_store.update_journal(path, lambda text: text + 'Attempted.\n')
    assert path.read_text() == latest
    assert org_transaction.journal_path().exists()


def test_failed_directory_flush_after_replacement_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / 'journal.md'
    original = b'Original.\r\n'
    path.write_bytes(original)
    real_flush = file_utils.fsync_directory
    failed = False
    def flush(directory):
        nonlocal failed
        if Path(directory) == tmp_path and not failed:
            failed = True
            raise OSError('injected directory flush failure')
        return real_flush(directory)
    monkeypatch.setattr(file_utils, 'fsync_directory', flush)
    with pytest.raises(OSError, match='directory flush failure'):
        journal_store.update_journal(path, lambda text: text + 'Pending.\n')
    assert failed
    assert path.read_bytes() == original
