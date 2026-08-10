"""min_bytes: the check for artifacts a daemon recreates when lost.

Regression cover for a real miss on 2026-08-10 -- moving the 2.6 GB lens DB
aside was NOT detected, because the KeepAlive daemon recreated it instantly
and both `exists` and `nonempty` passed on the fresh file.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from jobs.manifest import Artifact
from jobs.checks import run_check


def _artifact(path, arg):
    return Artifact(path=str(path), check="min_bytes", arg=arg, max_age_hours=None)


def test_passes_when_at_or_above_floor(tmp_path):
    f = tmp_path / "db"; f.write_bytes(b"x" * 100)
    assert run_check(_artifact(f, 100)) == []


def test_fails_when_below_floor(tmp_path):
    f = tmp_path / "db"; f.write_bytes(b"x" * 10)
    errs = run_check(_artifact(f, 100))
    assert len(errs) == 1 and "below the min_bytes floor" in errs[0]


def test_recreated_from_scratch_is_caught(tmp_path):
    """The exact miss: a daemon recreates the file, so it exists and is
    nonempty -- but it is a fraction of the accumulated size."""
    f = tmp_path / "observations.db"; f.write_bytes(b"SQLite header")
    assert run_check(Artifact(path=str(f), check="exists", arg=None, max_age_hours=None)) == []
    assert run_check(Artifact(path=str(f), check="nonempty", arg=None, max_age_hours=None)) == []
    assert run_check(_artifact(f, 1_000_000)) != []


def test_invalid_arg_reported_not_raised(tmp_path):
    f = tmp_path / "db"; f.write_bytes(b"x")
    errs = run_check(_artifact(f, "big"))
    assert len(errs) == 1 and "invalid arg" in errs[0]
