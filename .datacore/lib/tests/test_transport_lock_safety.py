"""Transport lock acquisition cannot alter an aliased file or expose state."""
import os

import pytest

import ledger_transport
from file_utils import private_state_directory


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_lock_alias_cannot_truncate_an_unrelated_file(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    locks = private_state_directory('locks')
    space = tmp_path / 'space'
    space.mkdir()
    victim = tmp_path / 'retained'
    victim.write_bytes(b'Irreplaceable fixture data\n')
    lock = locks / 'space.lock'
    if kind == 'symlink':
        lock.symlink_to(victim)
    else:
        os.link(victim, lock)
    try:
        with pytest.raises((ValueError, OSError)):
            with ledger_transport._repo_lock(space):
                pytest.fail('aliased lock entered the protected operation')
    finally:
        assert victim.read_bytes() == b'Irreplaceable fixture data\n'


def test_transport_preserves_existing_lock_inode_and_contents(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    lock = private_state_directory('locks') / 'space.lock'
    lock.write_text('Existing coordination inode\n')
    inode = lock.stat().st_ino
    for _ in range(2):
        with ledger_transport._repo_lock(tmp_path / 'space'):
            assert lock.stat().st_ino == inode
    assert lock.read_text() == 'Existing coordination inode\n'


def test_transport_creates_private_state_before_org_transactions(tmp_path, monkeypatch):
    from org_transaction import journal_path
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    with ledger_transport._repo_lock(tmp_path / 'space'):
        assert journal_path().parent.stat().st_mode & 0o077 == 0
