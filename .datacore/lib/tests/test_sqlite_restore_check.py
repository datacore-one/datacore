"""sqlite_restore_check.py: a backup copy is opened, not just sized (plan 2026-10-31,
phase H, backups 0). Each copy must pass SQLite's integrity check and hold, per
table, row counts within 5% of what the source had at push time (the counts
sqlite_push sends beside it). Written before the code.
"""
import json
import sqlite3

import sqlite_restore_check as R


def _copy(tmp_path, rows, counts=None, name="k.db"):
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t (v INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    c.commit()
    c.close()
    if counts is not None:
        (tmp_path / (name + ".counts.json")).write_text(json.dumps({"tables": counts}))
    return p


def test_a_good_copy_passes(tmp_path):
    assert R.check(_copy(tmp_path, 100, {"t": 102})) == ""


def test_rows_missing_beyond_five_percent_fail(tmp_path):
    why = R.check(_copy(tmp_path, 90, {"t": 100}))
    assert "t" in why and "90" in why and "100" in why


def test_a_missing_table_fails(tmp_path):
    assert "gone" in R.check(_copy(tmp_path, 10, {"t": 10, "other": 5}))


def test_no_counts_beside_it_is_not_a_pass(tmp_path):
    assert "counts" in R.check(_copy(tmp_path, 10))


def test_a_corrupt_copy_fails(tmp_path):
    p = tmp_path / "bad.db"
    p.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    (tmp_path / "bad.db.counts.json").write_text(json.dumps({"tables": {"t": 1}}))
    assert R.check(p)


def test_the_cli_says_ok_only_when_every_copy_passes(tmp_path, capsys):
    good = _copy(tmp_path, 10, {"t": 10}, "a.db")
    bad = _copy(tmp_path, 1, {"t": 10}, "b.db")
    assert R.main([str(good)]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("restore-check ok 1 copy")
    assert R.main([str(good), str(bad)]) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("restore-check FAILED 1 of 2")
