"""Re-indexing a file must not orphan its old FTS postings.

`files_fts` is an FTS5 external-content index kept in sync by the files_ai /
files_ad / files_au triggers. SQLite fires DELETE triggers for REPLACE conflict
resolution ONLY when `PRAGMA recursive_triggers` is ON -- it is OFF by default
and set nowhere here. So `INSERT OR REPLACE INTO files` deleted the old row
without ever firing `files_ad`, leaving its postings in the index forever, and
inserted the replacement under a fresh rowid.

Measured 2026-09-19 before the fix: 0-personal held 12,591 live files against
750,801 rows in the index (60x); nightshift's root DB held 28,506 against
3,671,599 (129x). A search for "nightshift" returned 28,519 raw index hits of
which 669 survived the join to `files`. The index had grown to 14GB on a host
at 87% disk. Results stayed correct -- the inner join discards orphans -- so
nothing failed loudly; only the disk did.

The guard is the invariant, not the size: one row in `files` means one row in
the index, however many times it has been re-indexed.
"""
import sqlite3

import pytest

import zettel_db
import zettel_processor


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point both modules at one throwaway DB and initialise the real schema."""
    path = tmp_path / "knowledge.db"
    monkeypatch.setattr(zettel_db, "get_db_path", lambda space=None: path)
    monkeypatch.setattr(zettel_processor, "get_connection",
                        lambda space=None: zettel_db.get_connection(space))
    zettel_db.init_database(None)
    return path


def _reindex(file_id, content):
    """Re-index one file through the production write path."""
    zettel_processor.save_to_database({
        "id": file_id, "path": f"/{file_id}.md", "space": "0-personal",
        "type": "zettel", "title": "t", "content": content, "summary": "s",
        "word_count": len(content.split()), "maturity": "seed", "is_stub": 0,
        "author": "test", "created_at": "2026-09-19", "updated_at": "2026-09-19",
        "terms": {}, "tags": [], "links": [], "entities": [], "references": [],
    })


def test_reindexing_does_not_orphan_fts_rows(db):
    body = " ".join(f"word{i}" for i in range(500))
    for run in range(25):
        _reindex("f1", f"{body} run{run}")

    conn = sqlite3.connect(db)
    live = conn.execute("select count(*) from files").fetchone()[0]
    indexed = conn.execute("select count(*) from files_fts_docsize").fetchone()[0]
    assert live == 1
    assert indexed == live, (
        f"{indexed} rows in the FTS index for {live} live file(s) -- "
        "re-indexing is orphaning postings again (see module docstring)"
    )


def test_search_matches_only_the_current_version(db):
    _reindex("f1", "aardvark")
    _reindex("f1", "buffalo")

    conn = sqlite3.connect(db)
    assert conn.execute(
        "select count(*) from files_fts where files_fts match 'buffalo'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from files_fts where files_fts match 'aardvark'"
    ).fetchone()[0] == 0, "a superseded version is still searchable"
