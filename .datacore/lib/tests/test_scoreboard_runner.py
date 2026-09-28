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
