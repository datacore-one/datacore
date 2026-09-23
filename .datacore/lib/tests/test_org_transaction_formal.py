"""Replays of the Lean model DatacoreSpec/OrgTransaction.lean against the real code.

race_*    -> theorems race_shipped_strands (refuted behaviour) / race_fixed_recovers
crash_*   -> theorem crash_recovers_pre_state (every crash point restores fs0)
stale_*   -> theorems stale_check_vacuous_first_touch / stale_check_detects_when_watched
"""
from pathlib import Path

import pytest
import org_transaction as tx
import safe_move


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str((tmp_path / 'state').resolve()))
    return tmp_path.resolve()


def lose_rename_race(monkeypatch, foreign='Competitor wrote this\n'):
    real = safe_move.rename_noreplace

    def racing(source, destination):
        Path(destination).write_text(foreign)  # the competitor wins the gap
        return real(source, destination)
    monkeypatch.setattr(safe_move, 'rename_noreplace', racing)


@pytest.mark.parametrize('watched_first', [True, False])
def test_race_lost_to_a_competitor_does_not_strand_the_journal(state, monkeypatch, watched_first):
    source, destination = state / 'inbox.org', state / 'archive.org'
    source.write_text('* DONE Finished\n')
    lose_rename_race(monkeypatch)

    @tx.serialized
    def archive():
        tx.watch_file(source)
        if watched_first:
            tx.watch_file(destination)
        tx.move_file(source, destination)

    with pytest.raises(FileExistsError):
        archive()
    assert source.read_text() == '* DONE Finished\n'
    assert destination.read_text() == 'Competitor wrote this\n'
    assert not tx.journal_path().exists()

    @tx.serialized
    def unrelated():
        tx.write_org_text(state / 'other.org', '* TODO Later work\n')
    unrelated()  # was: RecoveryRequired on this and every later call
    assert (state / 'other.org').read_text() == '* TODO Later work\n'


def test_race_rolls_back_earlier_writes_and_spares_the_competitor(state, monkeypatch):
    source, destination, log = state / 'inbox.org', state / 'archive.org', state / 'log.org'
    source.write_text('* DONE Finished\n')
    log.write_text('Original log\n')
    lose_rename_race(monkeypatch)

    @tx.serialized
    def archive():
        tx.watch_file(destination)
        tx.write_org_text(log, 'Archived one item\n')
        tx.move_file(source, destination)

    with pytest.raises(FileExistsError):
        archive()
    assert log.read_text() == 'Original log\n'
    assert source.read_text() == '* DONE Finished\n'
    assert destination.read_text() == 'Competitor wrote this\n'
    assert not tx.journal_path().exists()


class Crash(BaseException):
    """Process death: nothing in-process runs after it."""


def effects(monkeypatch, crash_at):
    """Kill the process at the crash_at-th durable file effect."""
    count = {'n': 0}

    def gate():
        count['n'] += 1
        if count['n'] == crash_at:
            raise Crash()
    real_write, real_rename, real_unlink = tx.atomic_write_text, safe_move.rename_noreplace, Path.unlink

    def write(path, content):
        gate(); return real_write(path, content)

    def rename(source, destination):
        gate(); return real_rename(source, destination)

    def unlink(self, *a, **k):
        if self.suffix == '.org':
            gate()
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(tx, 'atomic_write_text', write)
    monkeypatch.setattr(safe_move, 'rename_noreplace', rename)
    monkeypatch.setattr(Path, 'unlink', unlink)
    return count


@pytest.mark.parametrize('crash_at', range(1, 7))
def test_every_crash_point_recovers_the_pre_transaction_state(state, monkeypatch, crash_at):
    a, b, c, moved, gone = (state / f'{n}.org' for n in ('a', 'b', 'c', 'moved', 'gone'))
    a.write_text('A0\n'); b.write_text('B0\n'); c.write_text('C0\n'); gone.write_text('G0\n')
    before = {p: (p.read_text() if p.exists() else None) for p in (a, b, c, moved, gone)}
    count = effects(monkeypatch, crash_at)
    t = tx.Transaction(tx.journal_path())
    token = tx._current.set(t)
    try:
        tx.watch_file(c)
        tx.write_org_text(a, 'A1\n')
        tx.write_org_text(b, 'B1\n')
        tx.write_org_text(a, 'A2\n')
        tx.move_file(c, moved)
        tx.delete_file(gone)
        completed = True
    except Crash:
        completed = False
    finally:
        tx._current.reset(token)
    monkeypatch.undo()
    monkeypatch.setenv('DATACORE_STATE', str(state / 'state'))
    assert completed is (count['n'] < crash_at)
    tx.recover(tx.journal_path())  # the next serialized call
    assert {p: (p.read_text() if p.exists() else None) for p in before} == before
    assert not tx.journal_path().exists()


def test_stale_check_is_vacuous_when_the_path_is_first_touched_by_write(state):
    inbox = state / 'inbox.org'
    inbox.write_text('* TODO Original\n')

    @tx.serialized
    def unwatched():
        text = inbox.read_text()
        inbox.write_text('* TODO External edit\n')
        tx.write_org_text(inbox, text + '* TODO Added\n')
    unwatched()  # documents the caller contract: this silently loses the edit
    assert inbox.read_text() == '* TODO Original\n* TODO Added\n'

    inbox.write_text('* TODO Original\n')

    @tx.serialized
    def watched():
        text = tx.watch_file(inbox)['before']
        inbox.write_text('* TODO External edit\n')
        tx.write_org_text(inbox, text + '* TODO Added\n')
    with pytest.raises(tx.RecoveryRequired, match='stale overwrite'):
        watched()
    assert inbox.read_text() == '* TODO External edit\n'
