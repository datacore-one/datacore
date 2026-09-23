"""Owner follow-up decisions Q12 and Q14 (2026-09-23, second board).

Each test failed on the code before its decision was applied. Context:
`specs/datacore-lean/FOLLOWUPS.md` (B.11, B.13), `findings/org-transaction.md`
and `findings/org-tools.md`.

* Q12a `org_transaction.serialized` takes `timeout=`; org_date_hook uses it
       instead of wrapping `file_lock`.
* Q12b the manual repair tools write under `@serialized` with `watch_file` +
       `write_org_text`. The lost-update replay: an adapter commit that races
       the tool (from another thread, so it contends for the real lock) must
       survive. Before Q12b the tool wrote back its stale read over it.
* Q14  org_union_merge: a theirs-only item with no parent and a level deeper
       than 1 gets a created heading chain with a neutral text, never the last
       heading of the file as its parent.
"""
from __future__ import annotations

import contextvars
import random
import sys
import threading
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import inbox_dedup  # noqa: E402
import org_date_hook  # noqa: E402
import org_dedup_within_file  # noqa: E402
import org_resolve_id_conflicts  # noqa: E402
import org_transaction as tx  # noqa: E402
import org_union_merge  # noqa: E402
import stamp_seq_todo  # noqa: E402
import validate_org_dates  # noqa: E402
from file_utils import file_lock  # noqa: E402
from org_union_merge import reconcile, split  # noqa: E402

ADDED = "* TODO Added by adapter\n"


def _hold_lock(seconds):
    taken, done = threading.Event(), threading.Event()

    def run():
        with file_lock(tx.journal_path(), timeout=5):
            taken.set()
            done.wait(seconds)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert taken.wait(5)
    return t, done


# --- Q12a: serialized(timeout=) ----------------------------------------------

def test_serialized_accepts_a_timeout_and_gives_up_on_a_busy_lock():
    calls = []
    t, done = _hold_lock(10)
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            tx.serialized(timeout=0.2)(lambda: calls.append(1))()
        assert time.monotonic() - start < 2
    finally:
        done.set(); t.join()
    assert calls == []
    assert tx.serialized(timeout=0.2)(lambda: 7)() == 7


def test_serialized_default_timeout_is_unchanged(monkeypatch):
    seen = []
    real = tx.file_lock

    def spy(path, timeout=5.0, **kw):
        seen.append(timeout)
        return real(path, timeout=timeout, **kw)

    monkeypatch.setattr(tx, "file_lock", spy)

    @tx.serialized
    def a():
        return 1

    assert a() == 1 and tx.serialized(lambda: 2)() == 2
    assert seen == [30, 30]


def test_hook_uses_serialized_timeout_not_a_lock_wrapper(tmp_path, monkeypatch):
    assert not hasattr(org_date_hook, "_lock_timeout")
    seen = []
    real = tx.file_lock

    def spy(path, timeout=5.0, **kw):
        seen.append(timeout)
        return real(path, timeout=timeout, **kw)

    monkeypatch.setattr(tx, "file_lock", spy)
    monkeypatch.setattr(org_date_hook, "LOCK_TIMEOUT", 0.7)
    f = tmp_path / "n.org"
    f.write_text("<2026-09-24 Mon>\n")
    assert org_date_hook.fix_dates(str(f)) == 1
    assert seen == [0.7]
    assert tx.file_lock is spy          # never swapped out, even for one call


# --- Q12b: lost-update replay against each repair tool -----------------------

def _adapter_append(path: Path):
    """A concurrent adapter call: its own thread and a fresh context, so it
    contends for the real lock instead of joining the tool's transaction."""
    @tx.serialized
    def add():
        tx.watch_file(path)
        tx.write_org_text(path, (tx.read_text(path.resolve()) or "") + ADDED)

    t = threading.Thread(target=contextvars.Context().run, args=(add,), daemon=True)
    t.start()
    return t


@pytest.fixture
def race(monkeypatch):
    """Fire an adapter commit the first time the tool reads `target`, and give
    it up to 0.5 s to land before the tool continues. Unlocked, it lands and
    the tool's write-back of its stale read loses it; locked, it waits."""
    state = {"threads": []}
    real_read = Path.read_text

    def arm(target: Path):
        target = target.resolve()

        def read(self, *a, **k):
            text = real_read(self, *a, **k)
            if (not state["threads"] and threading.current_thread() is threading.main_thread()
                    and self.resolve() == target):
                t = _adapter_append(target)
                state["threads"].append(t)
                t.join(0.5)
            return text

        monkeypatch.setattr(Path, "read_text", read)

    def finish():
        monkeypatch.setattr(Path, "read_text", real_read)
        assert state["threads"], "the tool never read its target"
        for t in state["threads"]:
            t.join(10)
            assert not t.is_alive()
        assert not tx.journal_path().exists()

    state["arm"], state["finish"] = arm, finish
    return state


@pytest.fixture
def spy_writes(monkeypatch):
    seen = []
    real = tx.write_org_text
    monkeypatch.setattr(tx, "write_org_text",
                        lambda p, t: (seen.append(Path(p).resolve()), real(p, t))[1])
    return seen


DUP = "* TODO buy milk\nbody\n* TODO buy milk\nbody\n* TODO other\n"


def test_dedup_within_file_does_not_lose_a_racing_adapter_commit(tmp_path, race, spy_writes):
    f = tmp_path / "inbox.org"
    f.write_text(DUP)
    race["arm"](f)
    removed, _, _ = org_dedup_within_file.dedup(f, apply=True)
    race["finish"]()
    out = f.read_text()
    assert removed == 1 and out.count("buy milk") == 1
    assert ADDED in out                              # was lost before Q12b
    assert spy_writes[0] == f.resolve()


def test_dedup_within_file_dry_run_takes_no_lock(tmp_path):
    f = tmp_path / "inbox.org"
    f.write_text(DUP)
    t, done = _hold_lock(10)
    try:
        assert org_dedup_within_file.dedup(f, apply=False)[0] == 1
    finally:
        done.set(); t.join()
    assert f.read_text() == DUP


CONFLICT = ("* TODO a\n:PROPERTIES:\n" + "<" * 7 + " HEAD\n:ID: local\n" + "=" * 7 + "\n"
            ":ID: upstream\n" + ">" * 7 + " theirs\n:END:\n")


def test_resolve_id_conflicts_does_not_lose_a_racing_adapter_commit(tmp_path, race, spy_writes):
    f = tmp_path / "next_actions.org"
    f.write_text(CONFLICT)
    race["arm"](f)
    ok, msg = org_resolve_id_conflicts.resolve(f, apply=True, keep="other")
    race["finish"]()
    out = f.read_text()
    assert ok, msg
    assert ":ID: upstream" in out and "<" * 7 not in out
    assert ADDED in out
    assert spy_writes[0] == f.resolve()


def test_inbox_dedup_does_not_lose_a_racing_adapter_commit(tmp_path, race, spy_writes, monkeypatch):
    space = tmp_path / "sp"
    (space / "org").mkdir(parents=True)
    inbox = space / "org/inbox.org"
    inbox.write_text("* Inbox\n** TODO routed\n** TODO fresh\n")
    (space / "org/next_actions.org").write_text("* Work\n** TODO routed\n")
    (space / "org/research_learning.org").write_text("")
    monkeypatch.setattr(sys, "argv", ["inbox_dedup", "--root", str(tmp_path),
                                      "--space", "sp", "--apply"])
    race["arm"](inbox)
    assert inbox_dedup.main() == 0
    race["finish"]()
    out = inbox.read_text()
    assert "routed" not in out and "fresh" in out
    assert ADDED in out
    assert inbox.resolve() in spy_writes


def test_stamp_seq_todo_does_not_lose_a_racing_adapter_commit(tmp_path, race, spy_writes):
    f = tmp_path / "next_actions.org"
    f.write_text("#+TITLE: x\n* TODO a\n")
    race["arm"](f)
    assert stamp_seq_todo.stamp(f, dry_run=False).startswith("insert")
    race["finish"]()
    out = f.read_text()
    assert stamp_seq_todo.CANONICAL in out and ADDED in out
    assert spy_writes[0] == f.resolve()          # the tool first; the adapter waited


def test_validate_org_dates_fix_does_not_lose_a_racing_adapter_commit(tmp_path, race, spy_writes):
    f = tmp_path / "next_actions.org"
    f.write_text("* TODO a\nSCHEDULED: <2026-09-24 Mon>\n")
    race["arm"](f)
    assert validate_org_dates.main(["--fix", str(f)]) == 1
    race["finish"]()
    out = f.read_text()
    assert "<2026-09-24 Thu>" in out and ADDED in out
    assert spy_writes[0] == f.resolve()          # the tool first; the adapter waited


def test_union_merge_apply_does_not_lose_a_racing_adapter_commit(tmp_path, monkeypatch, spy_writes):
    """--apply reads the refs through git, not the working file, so the race
    fires on the second `git show`."""
    f = tmp_path / "inbox.org"
    f.write_text("* Inbox\n")
    shows = {"HEAD": "* Inbox\n** TODO mine\n", "origin/main": "* Inbox\n** TODO yours\n"}
    threads = []

    def show(ref, path, repo):
        if ref == "origin/main":
            t = _adapter_append(f)
            threads.append(t)
            t.join(0.5)
        return shows[ref]

    monkeypatch.setattr(org_union_merge, "_show", show)
    monkeypatch.setattr(sys, "argv", ["org_union_merge", "inbox.org", "--repo",
                                      str(tmp_path), "--apply"])
    assert org_union_merge.main() == 0
    threads[0].join(10)
    out = f.read_text()
    assert "mine" in out and "yours" in out
    assert ADDED in out
    assert spy_writes[0] == f.resolve()          # the tool first; the adapter waited


def test_union_merge_report_only_takes_no_lock(tmp_path, monkeypatch):
    shows = {"HEAD": "* Inbox\n", "origin/main": "* Inbox\n** TODO yours\n"}
    monkeypatch.setattr(org_union_merge, "_show", lambda ref, p, r: shows[ref])
    monkeypatch.setattr(sys, "argv", ["org_union_merge", "inbox.org", "--repo", str(tmp_path)])
    t, done = _hold_lock(10)
    try:
        start = time.monotonic()
        assert org_union_merge.main() == 0
        assert time.monotonic() - start < 2
    finally:
        done.set(); t.join()


# --- Q14: a parentless theirs-only item deeper than level 1 ------------------

def _parent_head(text, needle):
    """(level, heading line) of the nearest shallower heading before `needle`."""
    stack = []
    for _, block in split(text)[1]:
        head = block.splitlines()[0]
        level = len(head) - len(head.lstrip("*"))
        while stack and stack[-1][0] >= level:
            stack.pop()
        if needle in head:
            return stack[-1] if stack else None
        stack.append((level, head))
    raise AssertionError(needle)


def _item(level, iid):
    return f"{'*' * level} TODO {iid}\n:PROPERTIES:\n:ID: {iid}\n:END:\n"


def test_union_parentless_deep_item_gets_a_created_heading_not_the_last_one():
    ours = "* Q\n"
    theirs = _item(2, "x") + "* P\n"
    merged, stats = reconcile(ours, theirs)
    level, head = _parent_head(merged, "TODO x")
    assert head != "* Q"                                      # was "* Q"
    assert (level, head) == (1, "* " + org_union_merge.UNFILED)
    assert stats["headings_created"] == 1
    assert merged == "* Q\n* " + org_union_merge.UNFILED + "\n" + _item(2, "x") + "* P\n"


def test_union_parentless_item_two_levels_down_gets_a_chain():
    merged, stats = reconcile("* Q\n", _item(3, "x"))
    lines = [l for l in merged.splitlines() if l.startswith("*")]
    assert lines == ["* Q", "* " + org_union_merge.UNFILED,
                     "** " + org_union_merge.UNFILED, "*** TODO x"]
    assert stats["headings_created"] == 2


def test_union_parentless_siblings_share_the_created_heading():
    merged, stats = reconcile("* Q\n", _item(2, "a") + _item(2, "b") + _item(3, "c"))
    assert stats["headings_created"] == 1
    assert merged.count(org_union_merge.UNFILED) == 1
    assert _parent_head(merged, "TODO b")[1] == "* " + org_union_merge.UNFILED
    assert _parent_head(merged, "TODO c")[1] == "** TODO b"   # its own theirs-parent


def test_union_created_heading_has_no_state_no_id_and_no_collision():
    taken = org_union_merge.UNFILED
    ours = f"* {taken}\n** TODO keep\n"
    merged, _ = reconcile(ours, _item(2, "x"))
    level, head = _parent_head(merged, "TODO x")
    text = head.lstrip("*").strip()
    assert level == 1 and text != taken and text.startswith(taken)
    assert text.split()[0] not in ("TODO", "NEXT", "WAITING", "REVIEW", "DONE",
                                   "DEFERRED", "CANCELLED")
    block = next(b for _, b in split(merged)[1] if b.startswith(head + "\n"))
    assert ":ID:" not in block and block == head + "\n"


def test_union_created_heading_avoids_state_keyword_collisions_too():
    # A heading "TODO <UNFILED>" normalises to the same text.
    ours = f"* TODO {org_union_merge.UNFILED}\n"
    merged, _ = reconcile(ours, _item(2, "x"))
    assert _parent_head(merged, "TODO x")[1] != f"* TODO {org_union_merge.UNFILED}"
    assert _parent_head(merged, "TODO x")[1] != f"* {org_union_merge.UNFILED}"


def test_union_parentless_item_in_a_flat_file_stays_parentless_and_creates_nothing():
    """No heading in the output is shallower than the item, so end of file
    gives it no parent, as in theirs. Pinned before Q14 by
    test_org_union_merge.py::test_nothing_is_lost_from_either_side."""
    merged, stats = reconcile(_item(2, "a"), _item(2, "b") + _item(3, "c"))
    assert merged == _item(2, "a") + _item(2, "b") + _item(3, "c")
    assert not stats["headings_created"]


def test_union_level1_parentless_item_still_goes_to_eof_unchanged():
    merged, stats = reconcile("* Q\n", _item(1, "x"))
    assert merged == "* Q\n" + _item(1, "x") and not stats["headings_created"]


@pytest.mark.parametrize("seed", range(200))
def test_union_parentless_items_never_sit_under_an_existing_heading_randomised(seed):
    """`placeOrphan_parent_neutral` (DatacoreSpec/OrgTools.lean), checked on
    random outlines: every theirs-only item theirs has no parent for, deeper
    than level 1, ends up under a created heading (neutral text, no id), and
    nothing of either side is lost."""
    rng = random.Random(seed)

    def outline(ids, start_deep):
        parts, level = [], 0
        for n, iid in enumerate(ids):
            level = rng.randint(2, 3) if (n == 0 and start_deep) else rng.randint(1, min(level + 2, 4))
            if rng.random() < 0.2:
                parts.append(f"{'*' * rng.randint(1, max(1, level))} v{rng.randint(0, 2)}\n")
            parts.append(_item(level, iid))
        return "".join(parts)

    ours = outline([f"o{i}" for i in range(rng.randint(1, 4))], False)
    theirs = outline([f"t{i}" for i in range(rng.randint(1, 5))], rng.random() < 0.8)
    merged, _ = reconcile(ours, theirs)
    out_blocks = [b for _, b in split(merged)[1]]
    for side in (ours, theirs):
        blocks = [b for _, b in split(side)[1]]
        for blk in blocks:
            assert out_blocks.count(blk) >= blocks.count(blk)
    theirs_heads = [b.splitlines()[0] for _, b in split(theirs)[1]]
    for k, head in enumerate(theirs_heads):
        level = len(head) - len(head.lstrip("*"))
        if level < 2 or not head.startswith("*" * level + " TODO t"):
            continue
        if any(len(h) - len(h.lstrip("*")) < level for h in theirs_heads[:k]):
            continue                                      # has a theirs parent
        found = _parent_head(merged, head[level + 1:])
        # Parentless as in theirs, or under the created neutral heading;
        # never under a heading of either side.
        assert found is None or found[1].lstrip("*").strip() == org_union_merge.UNFILED, \
            (theirs, merged)
