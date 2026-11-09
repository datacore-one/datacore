"""Knowledge-cluster defects confirmed by the 2026-09-23 Lean models.

Model: specs/datacore-lean/DatacoreSpec/Knowledge.lean
Findings: specs/datacore-lean/findings/knowledge.md

* context_merge wrote a composed file containing the PRIVATE layer
  (`.local.md`) into a location git would track.
* prune_learning_buffer counted retired engrams as "promoted", and rewrote
  every kept entry (rstrip of '-', separators added, header newlines lost).
* briefing.actions: two concurrent `materialize` calls both appended the same
  `item.create` (check-then-act with no lock).
* agent_stream_store: (a) a row whose text holds U+2028 / U+2029 / U+0085 was
  written raw and then re-read with `splitlines()`, which splits inside the
  JSON string, so every later append to the stream raised; (b) a row skipped
  by its dedup_key did not reserve its id, so the same batch could carry that
  id with other content and the retry of an accepted batch raised
  EventConflict.
"""
from __future__ import annotations

import json
import subprocess
import threading
from datetime import date
from pathlib import Path

import pytest

import agent_stream_store as store
import context_merge
import prune_learning_buffer as plb


# --- context_merge: private content never reaches a tracked file -------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _layers(d: Path, local: bool = True) -> None:
    (d / "CLAUDE.base.md").write_text("# Base\npublic\n")
    if local:
        (d / "CLAUDE.local.md").write_text("# Private\nmy journal path\n")


def test_private_layer_is_not_written_where_git_would_track_it(tmp_path):
    _git(tmp_path, "init", "-q")
    _layers(tmp_path)
    ok, warnings = context_merge.rebuild_context(tmp_path)
    assert not (tmp_path / "CLAUDE.md").exists(), "private layer written to an unignored path"
    assert ok is False
    assert any("gitignore" in w.lower() for w in warnings)


def test_private_layer_is_written_when_the_output_is_ignored(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("CLAUDE.md\n*.local.md\n")
    _layers(tmp_path)
    ok, warnings = context_merge.rebuild_context(tmp_path)
    assert ok and warnings == []
    assert "my journal path" in (tmp_path / "CLAUDE.md").read_text()


def test_an_ignored_but_tracked_output_is_refused(tmp_path):
    """git check-ignore reports a tracked file as not ignored: a .gitignore
    line does not untrack a file that is already in the index."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "CLAUDE.md").write_text("old\n")
    _git(tmp_path, "add", "CLAUDE.md")
    (tmp_path / ".gitignore").write_text("CLAUDE.md\n")
    _layers(tmp_path)
    ok, _ = context_merge.rebuild_context(tmp_path)
    assert ok is False
    assert (tmp_path / "CLAUDE.md").read_text() == "old\n"


def test_public_only_output_is_written_anywhere(tmp_path):
    _git(tmp_path, "init", "-q")
    _layers(tmp_path, local=False)
    ok, warnings = context_merge.rebuild_context(tmp_path)
    assert ok and warnings == []
    assert (tmp_path / "CLAUDE.md").exists()


def test_outside_any_repository_the_output_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    _layers(tmp_path)
    ok, warnings = context_merge.rebuild_context(tmp_path)
    assert ok and warnings == []
    assert (tmp_path / "CLAUDE.md").exists()


# --- prune_learning_buffer ----------------------------------------------------


ENGRAMS = """engrams:
  - id: ENG-1
    version: 2
    status: retired
    statement: >-
      Always rotate backups before writing learning files anywhere
  - id: ENG-2
    version: 2
    status: active
    statement: "Prefer ripgrep over grep for repository wide searches"
"""


def _index(tmp_path):
    p = tmp_path / "engrams.yaml"
    p.write_text(ENGRAMS)
    return plb.load_engram_index(p)


def test_retired_engram_is_not_a_promotion(tmp_path):
    index = _index(tmp_path)
    assert plb.check_engram_exists(
        "Always rotate backups before writing learning files", index) is False
    assert plb.check_engram_exists(
        "Prefer ripgrep over grep for repository wide searches", index) is True


def test_status_after_statement_still_counts(tmp_path):
    p = tmp_path / "engrams.yaml"
    p.write_text("engrams:\n  - id: ENG-1\n    statement: rotate backups before writing learning files\n"
                 "    status: retired\n")
    assert plb.check_engram_exists("rotate backups before writing learning files",
                                   plb.load_engram_index(p)) is False


LEARNING = (
    "---\ntitle: patterns\n---\n# Patterns\n\n\n"
    "## 2025-01-01: promoted\n**Pattern**: Prefer ripgrep over grep for repository wide searches\n\n"
    "## 2025-01-02: kept, no separator, ends in dashes\n**Pattern**: unrelated fact about pumpkins\nflag --\n\n"
    "## 2025-01-03: kept with separator\n**Pattern**: another unrelated pumpkin fact\n\n---\n"
)


def test_kept_entries_and_header_are_byte_preserved(tmp_path):
    f = tmp_path / "patterns.md"
    f.write_text(LEARNING)
    stats = plb.prune_file(f, date(2026, 1, 1), _index(tmp_path))
    assert stats["pruned"] == 1
    header, rest = LEARNING.split("## 2025-01-01", 1)
    kept = "## 2025-01-02" + rest.split("## 2025-01-02", 1)[1]
    assert f.read_text() == header + kept


def test_nothing_pruned_leaves_the_file_untouched(tmp_path):
    f = tmp_path / "patterns.md"
    f.write_text(LEARNING)
    plb.prune_file(f, date(2024, 1, 1), _index(tmp_path))
    assert f.read_text() == LEARNING


# --- briefing.actions: concurrent materialize --------------------------------


@pytest.fixture
def _unsigned(monkeypatch, briefing_principals):
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)


def test_concurrent_materialize_appends_one_create(tmp_path, monkeypatch, _unsigned):
    import briefing.actions as actions
    from ledger.log import read_events

    space = tmp_path / "space"
    space.mkdir()
    real_fold = actions.fold
    first_folded, second_done = threading.Event(), threading.Event()
    calls = []

    def slow_fold(events):
        state = real_fold(events)
        calls.append(1)
        if len(calls) == 1:           # the first caller holds its stale snapshot
            first_folded.set()
            second_done.wait(1.0)
        return state

    monkeypatch.setattr(actions, "fold", slow_fold)
    results = {}

    def run(name):
        results[name] = actions.materialize([{"text": "Buy milk"}], space, "worker")
        if name == "b":
            second_done.set()

    a = threading.Thread(target=run, args=("a",))
    a.start()
    assert first_folded.wait(5)
    b = threading.Thread(target=run, args=("b",))
    b.start()
    a.join(10)
    b.join(10)
    creates = [e for e in read_events(space) if e.type == "item.create"]
    assert len(creates) == 1
    assert sorted(len(r.created) for r in results.values()) == [0, 1]


def test_rephrasing_beyond_case_and_whitespace_is_a_new_id():
    """The honest scope of the dedupe key (docstring corrected)."""
    from briefing.actions import item_id
    assert item_id("  Buy   MILK\n") == item_id("buy milk")
    assert item_id("Buy milk") != item_id("Buy the milk")


# --- agent_stream_store -------------------------------------------------------


@pytest.mark.parametrize("sep", [" ", " ", "\x85"])
def test_unicode_line_separator_does_not_brick_the_stream(tmp_path, sep):
    path = tmp_path / "events-2026-09-23.jsonl"
    rows = [{"id": "a", "summary": f"left{sep}right"}]
    assert store.append_events(path, rows) == 1
    assert store.append_events(path, rows) == 0          # retry is idempotent
    assert store.append_events(path, [{"id": "b"}]) == 1  # unrelated work goes on
    other_day = tmp_path / "events-2026-09-24.jsonl"
    assert store.append_events(other_day, [{"id": "c"}]) == 1
    lines = path.read_text(encoding="utf-8").split("\n")
    assert [json.loads(line)["id"] for line in lines if line] == ["a", "b"]


def test_blank_line_in_log_is_still_refused(tmp_path):
    """Behaviour kept: an empty line is malformed history, not skipped."""
    path = tmp_path / "events.jsonl"
    path.write_text('{"id": "a"}\n\n{"id": "b"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        store.append_events(path, [{"id": "c"}])


def test_dedup_skipped_row_still_reserves_its_id(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [{"id": "0", "dedup_key": "k"},
            {"id": "1", "dedup_key": "k", "summary": "first"},
            {"id": "1", "summary": "second"}]
    with pytest.raises(store.EventConflict):
        store.append_events(path, rows)
    assert not path.exists()


def test_accepted_batch_retry_is_idempotent(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [{"id": "0", "dedup_key": "k"},
            {"id": "1", "dedup_key": "k", "summary": "x"},
            {"id": "2", "summary": "y"}]
    assert store.append_events(path, rows) == 2
    before = path.read_bytes()
    assert store.append_events(path, rows) == 0
    assert path.read_bytes() == before
