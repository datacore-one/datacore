"""datacore#20: the unique (file_id, term) index on an old database.

The terms table was created without the constraint; the index came later.
A database that already held duplicate rows failed `CREATE UNIQUE INDEX`
with IntegrityError, which every SessionStart hook then inherited.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from zettel_db import _ensure_unique_terms_index  # noqa: E402

DDL = """CREATE TABLE terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL, term TEXT NOT NULL,
    frequency INTEGER DEFAULT 1, is_entity BOOLEAN DEFAULT 0, entity_type TEXT)"""


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(DDL)
    conn.executemany("INSERT INTO terms (file_id, term) VALUES (?, ?)", rows)
    return conn


def _index_exists(conn):
    return conn.execute("SELECT count(*) FROM sqlite_master WHERE type='index' "
                        "AND name='idx_terms_unique'").fetchone()[0] == 1


def test_old_database_with_duplicates_is_deduplicated_then_indexed():
    conn = _db([("f1", "gtd"), ("f1", "gtd"), ("f1", "gtd"), ("f2", "gtd"), ("f1", "plur")])
    removed = _ensure_unique_terms_index(conn.cursor())
    assert removed == 2
    assert _index_exists(conn)
    assert conn.execute("SELECT count(*) FROM terms").fetchone()[0] == 3
    # the first row of each pair survives
    assert conn.execute("SELECT min(id) FROM terms WHERE file_id='f1' AND term='gtd'").fetchone()[0] == 1


def test_clean_database_is_untouched():
    conn = _db([("f1", "a"), ("f2", "a")])
    assert _ensure_unique_terms_index(conn.cursor()) == 0
    assert _index_exists(conn)
    assert conn.execute("SELECT count(*) FROM terms").fetchone()[0] == 2


def test_index_then_refuses_new_duplicates():
    conn = _db([("f1", "a")])
    _ensure_unique_terms_index(conn.cursor())
    try:
        conn.execute("INSERT INTO terms (file_id, term) VALUES ('f1', 'a')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate accepted after the index exists")
