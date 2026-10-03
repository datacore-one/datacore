"""The scoreboard runner: a slow day must not turn the whole scoreboard red.

A batch's time limit used to cover every eval file of a suite at once. The
core suite has 160+ files and takes about ten minutes on a quiet machine, so
under load it ran past the limit and every promise in it counted red. The
runner now runs a suite in chunks, each with its own limit: a real hang reds
its own chunk only.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import promise_evals  # noqa: E402


def test_a_suite_runs_in_chunks_and_a_timeout_reds_only_its_chunk(monkeypatch, tmp_path):
    files = [tmp_path / f"test_promise_X{i}_thing.py" for i in range(1, 46)]
    calls: list[list[Path]] = []

    def fake_run_suite(cwd, chunk, env):
        calls.append(list(chunk))
        if files[0] in chunk:                      # this chunk "times out": all red
            return {f.name: False for f in chunk}
        return {f.name: True for f in chunk}

    monkeypatch.setattr(promise_evals, "run_suite", fake_run_suite)
    result = promise_evals.run_suite_chunked(tmp_path, files, {})

    assert len(calls) > 1
    assert all(len(c) <= promise_evals.CHUNK_FILES for c in calls)
    assert sorted(f for c in calls for f in c) == sorted(files)
    red = {n for n, ok in result.items() if not ok}
    assert red == {f.name for f in calls[0]}
    assert len(result) == len(files)


def test_the_first_run_keeps_each_failing_files_first_failure_line(tmp_path, monkeypatch):
    """MEM-51 on the Mac's 2026-10-03 board: red in the run, green when run again
    for its failure line, and the first run's reason was thrown away -- so the
    flake could not be named. The run now keeps it."""
    monkeypatch.setattr(promise_evals, "FIRST_FAILURES", {})
    bad = tmp_path / "test_promise_ZZ7_fixture.py"
    bad.write_text("def test_ok():\n    assert True\n\n"
                   "def test_host():\n    assert False, 'nightshift: could not read (ssh hiccup)'\n")
    good = tmp_path / "test_promise_ZZ8_fixture.py"
    good.write_text("def test_ok():\n    assert True\n")
    result = promise_evals.run_suite(tmp_path, [bad, good], {})
    assert result == {bad.name: False, good.name: True}
    [(test, line)] = promise_evals.FIRST_FAILURES[str(bad)]
    assert test == "test_host" and "ssh hiccup" in line and "\n" not in line
    assert str(good) not in promise_evals.FIRST_FAILURES
