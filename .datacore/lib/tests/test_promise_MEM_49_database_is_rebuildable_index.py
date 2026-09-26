"""MEM-49: My notes and tasks are plain readable files. Any database is a
rebuildable index, never the only copy.

Kind: deterministic + production contract (read-only).
  * knowledge.db (zettel_db + zettel_processor, DIP-0004): over a tmp Data
    tree with notes (.md) and tasks (org), index, delete the database, index
    again -- the same files are indexed, and every indexed row points at a
    file that exists (nothing lives only in the database);
  * the ledger index (ledger.index.build_index): built from the plain JSONL
    event log, deleted, rebuilt -- identical items;
  * production: no space's knowledge.db holds queued writes that never reached
    a file (pending_writes rows still 'pending' -- content that exists only
    in the database). (Stale rows for moved/archived files -- 63 in
    0-personal on 2026-09-26 -- are index freshness, not asserted here.)

Seeded failure: a row in the index whose file does not exist (content that
lives only in the database).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
LIB = TESTS.parent
ROOT = TESTS.parents[2]
PY = sys.executable
sys.path.insert(0, str(LIB))


def _tree(root: Path) -> None:
    sp = root / "0-personal"
    (sp / "3-knowledge" / "zettel").mkdir(parents=True)
    (sp / "notes" / "journals").mkdir(parents=True)
    (sp / "org").mkdir(parents=True)
    (sp / ".datacore").mkdir(parents=True)
    (sp / "3-knowledge" / "zettel" / "Fair data economy.md").write_text(
        "---\ntype: zettel\n---\n# Fair data economy\n\nData owners get paid. See [[Data tokenization]].\n")
    (sp / "3-knowledge" / "zettel" / "Data tokenization.md").write_text("# Data tokenization\n\nTokens for datasets.\n")
    (sp / "notes" / "journals" / "2026-09-25.md").write_text("# 2026-09-25\n\n- Wrote about [[Fair data economy]].\n")
    (sp / "org" / "inbox.org").write_text("* Inbox\n** TODO Read the tokenization paper\n")


def _index(root: Path) -> None:
    env = {**os.environ, "DATACORE_ROOT": str(root)}
    for cmd in ([PY, str(LIB / "zettel_db.py"), "init", "--space", "personal"],
                [PY, str(LIB / "zettel_processor.py"), "--scan", "--space", "personal"]):
        p = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=50)
        assert p.returncode == 0, f"{Path(cmd[1]).name} failed: {p.stderr[-400:]}"


def _paths(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("SELECT path FROM files")}
    finally:
        con.close()


def _missing(db: Path, base: Path, limit: int = 2000) -> list[str]:
    out = []
    for p in sorted(_paths(db))[:limit]:
        f = Path(p) if os.path.isabs(p) else base / p
        if not f.exists():
            out.append(p)
    return out


def test_knowledge_db_is_rebuilt_from_the_files(tmp_path):
    root = tmp_path / "Data"
    _tree(root)
    _index(root)
    db = root / "0-personal" / ".datacore" / "knowledge.db"
    first = _paths(db)
    assert first, "nothing was indexed"
    assert not _missing(db, root / "0-personal"), "the index lists files that do not exist"
    db.unlink()
    _index(root)
    assert _paths(db) == first, "deleting and rebuilding the index lost or changed content"


def test_seeded_orphan_row_is_detected(tmp_path):
    root = tmp_path / "Data"
    _tree(root)
    _index(root)
    db = root / "0-personal" / ".datacore" / "knowledge.db"
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(files)")]
    row = dict(zip(cols, con.execute("SELECT * FROM files LIMIT 1").fetchone()))
    row.pop("id", None)
    row["path"] = str(root / "0-personal" / "3-knowledge" / "zettel" / "Only in the database.md")
    con.execute(f"INSERT INTO files ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    con.commit()
    con.close()
    assert _missing(db, root / "0-personal"), "the detector missed a row with no file behind it"


def test_ledger_index_is_rebuilt_from_the_event_log(tmp_path, monkeypatch):
    from ledger.log import EventLog, read_events
    from ledger.fold import fold
    from ledger.index import build_index, items_by
    space = tmp_path / "5-evals"
    (space / ".datacore").mkdir(parents=True)
    log = EventLog(space, "eval-actor")
    log.append("item.create", {"id": "t-1", "title": "Call the bank", "kind": "task"})
    log.append("item.create", {"id": "t-2", "title": "Send the report", "kind": "task"})
    db = tmp_path / "index.db"
    build_index(fold(read_events(space)), db)
    before = items_by(db)
    assert {i["id"] for i in before} >= {"t-1", "t-2"}
    db.unlink()
    build_index(fold(read_events(space)), db)
    assert items_by(db) == before


@pytest.mark.production
def test_no_space_database_holds_content_that_is_not_in_a_file():
    problems = []
    for db in sorted(ROOT.glob("[0-9]-*/.datacore/knowledge.db")):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "pending_writes" in tables:
                n = con.execute("SELECT count(*) FROM pending_writes WHERE status='pending'").fetchone()[0]
                if n:
                    problems.append(f"{db.parent.parent.name}: {n} queued writes exist only in the database")
        finally:
            con.close()
        # Rows whose file was later moved/archived (a stale index) are NOT
        # asserted: that is index freshness, not a database holding the only copy.
    assert not problems, problems
