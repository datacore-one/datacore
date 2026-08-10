"""SQLite index over a `LedgerState` -- disposable, rebuildable.

This module builds a queryable SQLite index from a `ledger.fold.LedgerState`
(itself derived from `ledger.log.read_events()` + `ledger.fold.fold()`). The
database is an INDEX, not a source of truth: it is a derived projection that
exists purely to make ad-hoc queries (list items by status/owner, spend per
actor) cheap without re-folding the event log on every call. It is never
synced and is always safe to delete and rebuild from the event log.

Default location (used by the CLI -- callers of this module always pass an
explicit `db_path`, there is no default-path logic here):
`<DATACORE_ROOT>/.datacore/state/ledger/index.db`. That path is already
gitignored (`.datacore/state/` and, explicitly, `.datacore/state/ledger/` --
see Task 1.2 / the repo `.gitignore`).

Only `ItemState`'s `id`/`title`/`owner`/`status` and `LedgerState.spend` are
indexed, as the two tables `items(id, title, owner, status)` and
`spend(actor, cents)`. `ItemState.history` and `LedgerState.orphans` are
diagnostic-only (meant for humans reading the fold's reasoning, not for
querying by id/status/owner) and are deliberately NOT indexed.

`build_index` always `DROP TABLE IF EXISTS` before `CREATE TABLE`, so it is
safe to call against an existing db file left over from an old/different
schema -- there is no migration path, the index is simply rebuilt from
scratch every time. Because it is a pure function of `state` (no clock
reads, no randomness), calling it twice with an equal `state` produces
identical query results both times (idempotent rebuild).

sqlite3 stdlib only. Every query is parameterized (`?` placeholders) --
including the WHERE clauses `items_by` builds from its optional filters --
never f-string/format-built SQL. Connections are opened via
`contextlib.closing(sqlite3.connect(...))`, not bare
`with sqlite3.connect(...) as conn:` -- a `Connection` used directly as a
context manager only commits/rolls back the transaction on `__exit__`, it
does NOT close the connection (a well-known stdlib gotcha). `closing()`
guarantees `conn.close()` actually runs, so the on-disk file is never left
open/locked for the next caller.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .fold import LedgerState


def build_index(state: LedgerState, db_path: Path) -> None:
    """(Re)build the SQLite index at `db_path` from `state`.

    Drops and recreates `items(id TEXT PRIMARY KEY, title TEXT, owner TEXT,
    status TEXT)` and `spend(actor TEXT PRIMARY KEY, cents INTEGER)`, then
    populates them from `state.items` and `state.spend`. Creates `db_path`'s
    parent directories if they don't already exist.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(db_path)) as conn:
        with conn:  # transaction: commits on clean exit, rolls back on exception
            conn.execute("DROP TABLE IF EXISTS items")
            conn.execute("DROP TABLE IF EXISTS spend")
            conn.execute(
                "CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT, owner TEXT, status TEXT)"
            )
            conn.execute("CREATE TABLE spend (actor TEXT PRIMARY KEY, cents INTEGER)")

            conn.executemany(
                "INSERT INTO items (id, title, owner, status) VALUES (?, ?, ?, ?)",
                [(t.id, t.title, t.owner, t.status) for t in state.items.values()],
            )
            conn.executemany(
                "INSERT INTO spend (actor, cents) VALUES (?, ?)",
                list(state.spend.items()),
            )


def items_by(db_path: Path, status: str | None = None, owner: str | None = None) -> list[dict]:
    """Query `items`, optionally filtered by `status` and/or `owner`.

    Returns `[{"id", "title", "owner", "status"}, ...]` ordered by `id` for
    deterministic output regardless of insertion order. Filters are applied
    via a parameterized WHERE clause -- the SQL text only ever grows a fixed
    `"col = ?"` fragment per given filter, the filter *values* always travel
    as bound parameters, never interpolated into the query string.
    """
    clauses = []
    params: list[str] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if owner is not None:
        clauses.append("owner = ?")
        params.append(owner)

    sql = "SELECT id, title, owner, status FROM items"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"

    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [{"id": r[0], "title": r[1], "owner": r[2], "status": r[3]} for r in rows]


def spend_by_actor(db_path: Path) -> dict[str, int]:
    """Return `{actor: cents}` for every actor recorded in the `spend` table."""
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT actor, cents FROM spend").fetchall()
    return {actor: cents for actor, cents in rows}
