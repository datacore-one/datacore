"""Tests for ledger.index -- disposable, rebuildable SQLite index.

The index is a derived projection over `LedgerState` (itself derived from
`ledger.fold.fold` over real events, per `ledger.log`) -- never a source of
truth. These tests build state from REAL events (via `EventLog` + `fold`,
default unsigned) rather than hand-constructing `LedgerState`, so the index
is exercised against the same shape of data the CLI will actually feed it.
"""

import sqlite3

import pytest

from ledger.fold import fold
from ledger.index import build_index, spend_by_actor, items_by
from ledger.log import EventLog, read_events


@pytest.fixture(autouse=True)
def _no_signing_env(monkeypatch):
    # Keep these tests hermetic regardless of the ambient environment --
    # unsigned is the EventLog default when `sign=` is omitted AND
    # DATACORE_LEDGER_SIGN isn't set.
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)


def _mk_log(tmp_path, actor):
    return EventLog(
        tmp_path / "space",
        actor,
        keys_dir=tmp_path / "keys",
        registry_path=tmp_path / "registry.yaml",
    )


def _folded_state(tmp_path):
    """Two actors, two items, three spend records -- built via real
    EventLog.append() calls (default unsigned) and reduced with fold()."""
    mac = _mk_log(tmp_path, "mac")
    pi = _mk_log(tmp_path, "pi")

    mac.append("item.create", {"id": "t1", "title": "Ship it"})
    mac.append("item.claim", {"id": "t1"})
    mac.append("item.complete", {"id": "t1"})

    pi.append("item.create", {"id": "t2", "title": "Review docs"})
    pi.append("item.claim", {"id": "t2"})

    mac.append("spend.record", {"cents": 500, "ref": "invoice-1"})
    pi.append("spend.record", {"cents": 250, "ref": "invoice-2"})
    mac.append("spend.record", {"cents": 100, "ref": "invoice-3"})

    events = read_events(tmp_path / "space")
    return fold(events)


_EXPECTED_ITEMS = [
    {"id": "t1", "title": "Ship it", "owner": "mac", "status": "completed"},
    {"id": "t2", "title": "Review docs", "owner": "pi", "status": "claimed"},
]
_EXPECTED_SPEND = {"mac": 600, "pi": 250}


# --- build + query from a real folded state ---------------------------------


def test_build_index_then_query(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"

    build_index(state, db_path)

    assert items_by(db_path) == _EXPECTED_ITEMS
    assert spend_by_actor(db_path) == _EXPECTED_SPEND


def test_build_index_creates_parent_dirs(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "nested" / "dir" / "index.db"
    assert not db_path.parent.exists()

    build_index(state, db_path)

    assert db_path.exists()


# --- rebuild idempotency -----------------------------------------------------


def test_rebuild_idempotent(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"

    build_index(state, db_path)
    first_items = items_by(db_path)
    first_spend = spend_by_actor(db_path)

    build_index(state, db_path)  # same input state, second run
    second_items = items_by(db_path)
    second_spend = spend_by_actor(db_path)

    assert first_items == second_items == _EXPECTED_ITEMS
    assert first_spend == second_spend == _EXPECTED_SPEND


def test_build_index_safe_on_existing_db_with_old_schema(tmp_path):
    """build_index must DROP TABLE IF EXISTS first -- safe to run against an
    existing db file left over from a stale/different schema."""
    db_path = tmp_path / "index.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (weird_column TEXT)")
        conn.execute("INSERT INTO items VALUES ('stale-row')")
        conn.execute("CREATE TABLE spend (nonsense TEXT)")
        conn.commit()

    state = _folded_state(tmp_path)
    build_index(state, db_path)

    assert items_by(db_path) == _EXPECTED_ITEMS
    assert spend_by_actor(db_path) == _EXPECTED_SPEND


# --- filter combinations ------------------------------------------------------


def test_filter_by_status_only(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    assert items_by(db_path, status="claimed") == [
        {"id": "t2", "title": "Review docs", "owner": "pi", "status": "claimed"},
    ]


def test_filter_by_owner_only(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    assert items_by(db_path, owner="mac") == [
        {"id": "t1", "title": "Ship it", "owner": "mac", "status": "completed"},
    ]


def test_filter_by_status_and_owner(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    assert items_by(db_path, status="completed", owner="mac") == [
        {"id": "t1", "title": "Ship it", "owner": "mac", "status": "completed"},
    ]
    # cross combo that matches nothing
    assert items_by(db_path, status="claimed", owner="mac") == []


def test_filter_none_returns_all_ordered_by_id(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    rows = items_by(db_path)
    assert [r["id"] for r in rows] == ["t1", "t2"]  # ordered, not insertion order


# --- spend --------------------------------------------------------------------


def test_spend_by_actor_roundtrip(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    assert spend_by_actor(db_path) == _EXPECTED_SPEND


# --- empty state ----------------------------------------------------------------


def test_empty_state_returns_empty(tmp_path):
    state = fold([])
    db_path = tmp_path / "index.db"

    build_index(state, db_path)

    assert items_by(db_path) == []
    assert spend_by_actor(db_path) == {}


# --- history/orphans deliberately NOT indexed ------------------------------------


def test_only_items_and_spend_tables_exist(tmp_path):
    """ItemState.history and LedgerState.orphans are diagnostic-only and
    must NOT be indexed -- the db's table list is exactly {items, spend}."""
    mac = _mk_log(tmp_path, "mac")
    mac.append("item.claim", {"id": "ghost"})  # produces an orphan
    mac.append("item.create", {"id": "t1", "title": "Has history"})
    mac.append("item.claim", {"id": "t1"})

    events = read_events(tmp_path / "space")
    state = fold(events)
    assert state.orphans  # sanity: an orphan was actually produced
    assert state.items["t1"].history  # sanity: history was actually populated

    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert tables == {"items", "spend"}


# --- parameterized queries / no f-string SQL -----------------------------------


def test_filter_values_are_not_sql_injectable(tmp_path):
    """A filter value containing SQL metacharacters must be treated as
    literal data (parameterized query) -- never interpolated into the SQL
    string. This would corrupt/drop the table if queries were built with
    f-strings instead of `?` placeholders."""
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)

    assert items_by(db_path, status="'; DROP TABLE items; --") == []
    # table must still exist and be queryable afterward
    assert items_by(db_path) == _EXPECTED_ITEMS


# --- connection hygiene: no leftover WAL/journal files, no locking -------------


def test_no_leftover_journal_or_wal_files(tmp_path):
    state = _folded_state(tmp_path)
    db_path = tmp_path / "idxdir" / "index.db"

    build_index(state, db_path)
    items_by(db_path)
    spend_by_actor(db_path)

    leftover = sorted(p.name for p in db_path.parent.iterdir() if p.name != db_path.name)
    assert leftover == []


def test_db_file_not_locked_after_calls(tmp_path):
    """Every function must close its connection -- a fresh connection from
    outside must be able to open and write immediately afterward."""
    state = _folded_state(tmp_path)
    db_path = tmp_path / "index.db"
    build_index(state, db_path)
    items_by(db_path)
    spend_by_actor(db_path)

    with sqlite3.connect(db_path, timeout=0) as conn:
        conn.execute("CREATE TABLE probe (x TEXT)")
        conn.commit()
