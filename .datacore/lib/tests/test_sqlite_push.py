"""sqlite_push.py: a consistent SQLite snapshot, pushed over the ssh alias, proven.

The jobs this replaced (2026-09-28) rsynced live databases, WAL and all, to a
raw IP as root into a path nothing read; one also shipped a local auth token.
These tests hold the replacement to: a snapshot that includes committed-but-
uncheckpointed writes and stands alone (no -wal needed), a push that goes
through the roster's ssh alias into the remote user's home, a result line the
job contract can read, and a failure that says so instead of passing.
"""
from __future__ import annotations

import sqlite3

import pytest

import sqlite_push as S


def _wal_db(path, rows=3):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE t (v INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    c.commit()
    return c  # kept open: the rows sit in the -wal, not yet in the main file


def test_snapshot_is_consistent_and_stands_alone(tmp_path):
    src = tmp_path / "live.db"
    keep = _wal_db(src)
    snap = tmp_path / "snap" / "live.db"
    size = S.snapshot(src, snap)
    keep.close()
    assert size == snap.stat().st_size > 0
    c = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 3
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not snap.with_name("live.db-wal").exists()


def test_snapshot_recovers_a_hot_journal_left_by_a_dead_writer(tmp_path):
    """A writer killed mid-transaction leaves db + hot -journal. Every normal
    opener rolls it back; a read-only one cannot and fails with 'attempt to
    write a readonly database' (the personal knowledge index, 2026-09-28)."""
    live = tmp_path / "live" / "k.db"
    live.parent.mkdir()
    c = sqlite3.connect(live, isolation_level=None)
    c.execute("CREATE TABLE t (v TEXT)")
    c.execute("INSERT INTO t VALUES ('committed')")
    c.execute("PRAGMA cache_size=10")  # spill pages into the db file mid-
    c.execute("PRAGMA cache_spill=10")  # transaction, so the journal is hot
    c.execute("BEGIN")
    c.executemany("INSERT INTO t VALUES (?)", [("x" * 500,) for _ in range(5000)])
    crashed = tmp_path / "crashed"
    crashed.mkdir()
    for name in ("k.db", "k.db-journal"):
        (crashed / name).write_bytes((live.parent / name).read_bytes())
    c.execute("ROLLBACK")
    c.close()
    assert (crashed / "k.db-journal").stat().st_size > 0
    snap = tmp_path / "snap" / "k.db"
    S.snapshot(crashed / "k.db", snap)
    s = sqlite3.connect(snap)
    assert s.execute("SELECT v FROM t").fetchall() == [("committed",)]


def test_snapshot_never_creates_a_missing_source(tmp_path):
    with pytest.raises(sqlite3.Error):
        S.snapshot(tmp_path / "absent.db", tmp_path / "snap.db")
    assert not (tmp_path / "absent.db").exists()


class _Remote:
    """Stands in for ssh/rsync: records every command, answers stat with a size."""

    def __init__(self, size=None, fail=None):
        self.calls, self.size, self.fail = [], size, fail

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc, out = 0, ""
        if self.fail and self.fail in argv[0]:
            rc = 23
        elif argv[0] == "ssh" and "stat" in " ".join(argv):
            out = f"{self.size}\n"
        return type("P", (), {"returncode": rc, "stdout": out, "stderr": "boom" if rc else ""})()


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "observations.db"
    _wal_db(p).close()
    return p


def _main(monkeypatch, tmp_path, src, remote, dest=".datacore/lens/observations.db"):
    monkeypatch.setattr(S, "_alias", lambda machine: {"box": "box-alias"}.get(machine))
    monkeypatch.setattr(S, "_run", remote)
    monkeypatch.setenv("SQLITE_PUSH_TMP", str(tmp_path / "work"))
    return S.main([str(src), "box", dest])


def test_push_goes_through_the_alias_and_reports_ok(monkeypatch, tmp_path, src, capsys):
    remote = _Remote()
    remote.size = None  # filled below once the snapshot size is known
    real = remote.__call__

    def answer(argv, **kw):
        if argv[0] == "rsync":
            remote.size = __import__("os").path.getsize(argv[-2])
        return real(argv, **kw)

    assert _main(monkeypatch, tmp_path, src, answer) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith("sqlite_push: ok ")
    assert f"bytes={remote.size}" in line and f"remote_bytes={remote.size}" in line
    flat = [" ".join(c) for c in remote.calls]
    assert any(c.startswith("rsync ") and c.endswith("box-alias:.datacore/lens/observations.db") for c in flat)
    assert all("root@" not in c and "/root/" not in c for c in flat)
    assert not any((tmp_path / "work").rglob("*.db")), "the snapshot is removed after the push"


def test_a_size_mismatch_is_a_failure(monkeypatch, tmp_path, src, capsys):
    assert _main(monkeypatch, tmp_path, src, _Remote(size=1)) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("sqlite_push: FAILED")


def test_a_failed_rsync_is_a_failure_and_cleans_up(monkeypatch, tmp_path, src, capsys):
    assert _main(monkeypatch, tmp_path, src, _Remote(fail="rsync")) == 1
    assert "FAILED" in capsys.readouterr().out
    assert not any((tmp_path / "work").rglob("*.db"))


def test_missing_source_and_unknown_machine_fail(monkeypatch, tmp_path, src, capsys):
    assert _main(monkeypatch, tmp_path, tmp_path / "absent.db", _Remote()) == 1
    monkeypatch.setattr(S, "_alias", lambda machine: None)
    monkeypatch.setattr(S, "_run", _Remote())
    assert S.main([str(src), "nowhere", "x.db"]) == 1
    assert capsys.readouterr().out.count("FAILED") == 2


@pytest.mark.parametrize("dest", ["/root/.datacore/lens/observations.db", "../elsewhere.db", "dir/"])
def test_the_destination_stays_inside_the_remote_home(monkeypatch, tmp_path, src, dest, capsys):
    remote = _Remote()
    assert _main(monkeypatch, tmp_path, src, remote, dest=dest) == 1
    assert remote.calls == [], "nothing is sent to a path outside the remote user's home"
