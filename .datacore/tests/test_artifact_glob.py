"""A glob artifact path must resolve to a real file (2026-09-08).

`_artifact_path` never expanded globs: the path came back containing a
literal `*`, which nothing is named, so every job declaring one reported
"artifact absent after run" forever. Three did, and none could ever have
passed — a check that cannot succeed teaches an operator to ignore it."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib" / "jobs"
sys.path.insert(0, str(LIB))
import run as jr  # noqa: E402


def test_a_glob_resolves_to_the_newest_match(tmp_path, monkeypatch):
    monkeypatch.setattr(jr, "HOME", tmp_path)
    d = tmp_path / ".datacore" / "cos" / "agent-stream"
    d.mkdir(parents=True)
    old, new = d / "events-2026-05-08.jsonl", d / "events-2026-09-08.jsonl"
    old.write_text("x"); new.write_text("y")
    os.utime(old, (time.time() - 90000, time.time() - 90000))
    got = jr._artifact_path("~/.datacore/cos/agent-stream/events-*.jsonl")
    assert got == new, "the freshness window is the point — take the newest"


def test_a_directory_glob_resolves_too(tmp_path, monkeypatch):
    monkeypatch.setattr(jr, "HOME", tmp_path)
    for day in ("2026-09-07", "2026-09-08"):
        p = tmp_path / ".datacore" / "cos" / "briefings" / day
        p.mkdir(parents=True)
        (p / "app-briefing.json").write_text("{}")
    got = jr._artifact_path("~/.datacore/cos/briefings/*/app-briefing.json")
    assert got.exists() and got.parent.name in ("2026-09-07", "2026-09-08")


def test_an_unmatched_glob_stays_literal_so_the_message_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(jr, "HOME", tmp_path)
    got = jr._artifact_path("~/.datacore/nothing/events-*.jsonl")
    assert "*" in str(got) and not got.exists()


def test_a_plain_path_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(jr, "HOME", tmp_path)
    assert jr._artifact_path("~/a/b.log") == tmp_path / "a" / "b.log"
    assert jr._artifact_path("/tmp/x.log") == Path("/tmp/x.log")
