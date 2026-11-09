"""sprint_sync: a queue entry whose source task no longer exists is pruned once.

On 2026-10-01 the overnight run refused eleven 5-plur queue entries, every run,
because their SOURCE_ID resolved to no task: the tasks had been completed and
left the projection, the sprints that queued them were over, and nothing removed
the entries. sprint_sync only touched the queue while a sprint was active.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import sprint_sync  # noqa: E402

HEADER = sprint_sync.QUEUE_HEADER.format(space="9-test", slug="9-test")


def entry(qid, source, state="NEXT"):
    return (f"** {state} Work for {source} :AI:\n"
            f"  :PROPERTIES:\n"
            f"  :ID: {qid}\n"
            f"  :SOURCE_ID: {source}\n"
            f"  :SPACE: 9-test\n"
            f"  :SPRINT: 2026-W37-old\n"
            f"  :END:\n"
            f"  Reference entry. The specification lives on {source}.\n")


def task(tid, state="TODO"):
    return (f"* {state} Live task {tid} :AI:\n"
            f"  :PROPERTIES:\n"
            f"  :ID: {tid}\n"
            f"  :END:\n")


def make(tmp_path, *, tasks=("live-1",), archived=(), entries=()):
    org = tmp_path / "9-test" / "org"
    org.mkdir(parents=True)
    if tasks is not None:
        (org / "next_actions.org").write_text(
            "#+TITLE: Next\n" + "".join(task(t) for t in tasks))
    if archived:
        (org / "next_actions_archive.org").write_text(
            "".join(task(t, "DONE") for t in archived))
    (org / "nightshift.org").write_text(HEADER + "".join(entry(*e) for e in entries))
    return org


def ids_in(path):
    return [line.split(":ID:")[1].strip() for line in path.read_text().splitlines()
            if ":ID:" in line]


def test_a_dead_entry_is_pruned_and_a_live_one_kept(tmp_path, capsys):
    org = make(tmp_path, entries=[("live-1-q", "live-1"), ("gone-1-q", "gone-1")])
    pruned = sprint_sync.prune_dead_entries("9-test", apply=True, root=tmp_path)
    assert [p[0] for p in pruned] == ["gone-1-q"]
    left = ids_in(org / "nightshift.org")
    assert "live-1-q" in left and "gone-1-q" not in left
    # The task itself is never touched.
    assert "live-1" in ids_in(org / "next_actions.org")


def test_dry_run_writes_nothing(tmp_path):
    org = make(tmp_path, entries=[("gone-1-q", "gone-1")])
    before = (org / "nightshift.org").read_text()
    pruned = sprint_sync.prune_dead_entries("9-test", apply=False, root=tmp_path)
    assert [p[0] for p in pruned] == ["gone-1-q"]
    assert (org / "nightshift.org").read_text() == before


def test_limit_prunes_one_first(tmp_path):
    org = make(tmp_path, entries=[("gone-1-q", "gone-1"), ("gone-2-q", "gone-2")])
    pruned = sprint_sync.prune_dead_entries("9-test", apply=True, root=tmp_path, limit=1)
    assert len(pruned) == 1
    left = ids_in(org / "nightshift.org")
    assert sum(q in left for q in ("gone-1-q", "gone-2-q")) == 1


def test_a_task_that_still_exists_anywhere_keeps_its_entry(tmp_path):
    # Archived, or in another space: it exists, so this is not ours to prune.
    org = make(tmp_path, archived=("old-1",), entries=[("old-1-q", "old-1"),
                                                        ("other-1-q", "other-1")])
    other = tmp_path / "8-other" / "org"
    other.mkdir(parents=True)
    (other / "next_actions.org").write_text(task("other-1"))
    assert sprint_sync.prune_dead_entries("9-test", apply=True, root=tmp_path) == []
    assert {"old-1-q", "other-1-q"} <= set(ids_in(org / "nightshift.org"))


def test_without_a_readable_task_file_nothing_is_pruned(tmp_path):
    # A missing projection is not proof that every task is gone.
    org = make(tmp_path, tasks=None, entries=[("gone-1-q", "gone-1")])
    assert sprint_sync.prune_dead_entries("9-test", apply=True, root=tmp_path) == []
    assert "gone-1-q" in ids_in(org / "nightshift.org")


def test_closed_entries_are_left_alone(tmp_path):
    org = make(tmp_path, entries=[("gone-1-q", "gone-1", "CANCELLED")])
    assert sprint_sync.prune_dead_entries("9-test", apply=True, root=tmp_path) == []
    assert "gone-1-q" in ids_in(org / "nightshift.org")


def test_main_prunes_even_with_no_active_sprint(tmp_path, monkeypatch, capsys):
    org = make(tmp_path, tasks=("live-1", "live-2"),
               entries=[("live-1-q", "live-1"), ("live-2-q", "live-2"), ("gone-1-q", "gone-1")])
    monkeypatch.setattr(sprint_sync, "REPO", tmp_path)
    monkeypatch.setattr(sprint_sync, "pick", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", ["sprint_sync", "--space", "9-test", "--active", "--apply"])
    assert sprint_sync.main() == 0
    out = capsys.readouterr().out
    assert "gone-1-q" in out and "pruned" in out
    # run.py hides output that says "no sprint selected"; a prune must be seen.
    assert "no sprint selected" not in out
    assert "gone-1-q" not in ids_in(org / "nightshift.org")
