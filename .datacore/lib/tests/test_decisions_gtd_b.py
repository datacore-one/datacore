"""The owner's decisions G8-G12 (2026-09-23), applied to the org repair tools.

Each test failed on the code before its decision was applied. The decisions
and their Lean models are in `specs/datacore-lean/DECISIONS.md`,
`DatacoreSpec/OrgTools.lean` and `DatacoreSpec/Dates.lean`.

* G8  the live writers (org_date_hook, triage_utils) take the org transaction
      lock and write atomically; the hook never blocks long and never raises.
* G9  org_dedup_within_file dismisses (housekeeping) a dropped copy's id.
* G10 org_resolve_id_conflicts dismisses (housekeeping) a discarded id the
      ledger created.
* G11 triage_utils never appends to a DEFERRED task.
* G12 org_union_merge creates the missing heading instead of placing an item
      under an unrelated one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import org_date_hook  # noqa: E402
import org_dedup_within_file  # noqa: E402
import org_resolve_id_conflicts  # noqa: E402
import org_transaction as tx  # noqa: E402
import triage_utils  # noqa: E402
from file_utils import file_lock  # noqa: E402
from org_union_merge import reconcile, split  # noqa: E402


def _hold_lock(seconds):
    """Hold the org transaction lock from another thread (its own fd)."""
    taken, done = threading.Event(), threading.Event()

    def run():
        with file_lock(tx.journal_path(), timeout=5):
            taken.set()
            done.wait(seconds)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert taken.wait(5)
    return t, done


# --- G8: org_date_hook -------------------------------------------------------

def test_hook_does_not_lose_an_adapter_commit(tmp_path, monkeypatch):
    """The lost-update replay from findings/org-transaction.md, candidate 5.

    A serialized adapter call commits between the hook's read and its write.
    Before G8 the hook wrote back its fixed copy of the stale read."""
    f = (tmp_path / "next_actions.org").resolve()
    f.write_text("* TODO Call back\nSCHEDULED: <2026-09-24 Mon>\n")
    real_read = Path.read_text
    fired = []

    def read_then_adapter_commits(self, *a, **k):
        text = real_read(self, *a, **k)
        if self == f and not fired:
            fired.append(1)

            @tx.serialized
            def add():
                tx.watch_file(f)
                tx.write_org_text(f, real_read(f) + "* TODO Added by adapter\n")
            add()
        return text

    monkeypatch.setattr(Path, "read_text", read_then_adapter_commits)
    n = org_date_hook.fix_dates(str(f))
    monkeypatch.setattr(Path, "read_text", real_read)
    out = f.read_text()
    assert fired
    assert "Added by adapter" in out          # the adapter commit survived
    assert "<2026-09-24 Thu>" in out and n == 1
    assert not tx.journal_path().exists()


def test_hook_skips_with_one_warning_when_the_lock_is_busy(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(org_date_hook, "LOCK_TIMEOUT", 0.3)
    f = tmp_path / "n.org"
    f.write_text("<2026-09-24 Mon>\n")
    t, done = _hold_lock(10)
    try:
        start = time.monotonic()
        n = org_date_hook.fix_dates(str(f))
        elapsed = time.monotonic() - start
    finally:
        done.set(); t.join()
    assert n == 0 and elapsed < 3
    assert f.read_text() == "<2026-09-24 Mon>\n"     # skipped, not written
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "skipped" in err[0]


def test_hook_writes_through_the_transaction(tmp_path, monkeypatch):
    seen = []
    real = tx.write_org_text
    monkeypatch.setattr(tx, "write_org_text", lambda p, t: (seen.append(Path(p).resolve()), real(p, t)))
    f = tmp_path / "n.md"
    f.write_text("2026-09-24 Mon\n")
    assert org_date_hook.fix_dates(str(f)) == 1
    assert seen == [f.resolve()] and f.read_text() == "2026-09-24 Thu\n"


def test_hook_takes_no_lock_when_there_is_nothing_to_fix(tmp_path, monkeypatch):
    f = tmp_path / "n.md"
    f.write_text("2026-09-24 Thu\n")
    t, done = _hold_lock(10)
    try:
        start = time.monotonic()
        assert org_date_hook.fix_dates(str(f)) == 0
        assert time.monotonic() - start < 0.2
    finally:
        done.set(); t.join()


def test_hook_main_never_raises_into_the_session(tmp_path):
    """A retained journal makes every serialized call raise RecoveryRequired.
    The hook must still exit 0 and leave the file alone."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "org-transaction.json").write_text("not json")
    f = tmp_path / "n.org"
    f.write_text("<2026-09-24 Mon>\n")
    env = {**os.environ, "DATACORE_STATE": str(state.resolve())}
    r = subprocess.run([sys.executable, str(LIB / "org_date_hook.py")],
                       input=json.dumps({"tool_input": {"file_path": str(f)}}),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    assert f.read_text() == "<2026-09-24 Mon>\n"
    assert len(r.stderr.strip().splitlines()) == 1


# --- G8: triage_utils ---------------------------------------------------------

TASK = ("* Focus\n** TODO Respond to repo#1\n  :PROPERTIES:\n  :ID: node-1\n  :END:\n")


@pytest.mark.parametrize("call", ["append", "props"])
def test_triage_writers_wait_for_the_lock(tmp_path, call):
    f = tmp_path / "next_actions.org"
    f.write_text(TASK)
    t, done = _hold_lock(10)
    timer = threading.Timer(0.5, done.set)
    timer.start()
    start = time.monotonic()
    if call == "append":
        triage_utils._append_task_body(f, "Respond to repo#1", "context")
    else:
        triage_utils._set_task_properties(f, "node-1", {"TRIAGE_ID": "gh-1"})
    elapsed = time.monotonic() - start
    t.join()
    assert elapsed >= 0.4                       # it waited for the holder
    text = f.read_text()
    assert ("   context" in text) if call == "append" else (":TRIAGE_ID:" in text)
    assert not tx.journal_path().exists()


def test_triage_writes_through_the_transaction(tmp_path, monkeypatch):
    seen = []
    real = tx.write_org_text
    monkeypatch.setattr(tx, "write_org_text", lambda p, t: (seen.append(Path(p).resolve()), real(p, t)))
    f = tmp_path / "next_actions.org"
    f.write_text(TASK)
    triage_utils._set_task_properties(f, "node-1", {"TRIAGE_ID": "gh-1"})
    triage_utils._append_task_body(f, "Respond to repo#1", "context")
    assert seen == [f.resolve(), f.resolve()]


# --- G11: triage_utils skips DEFERRED -----------------------------------------

def test_triage_never_appends_to_a_deferred_task(tmp_path):
    f = tmp_path / "next_actions.org"
    text = "* Focus\n** DEFERRED Respond to repo#1\n  :PROPERTIES:\n  :ID: d\n  :END:\n"
    f.write_text(text)
    triage_utils._append_task_body(f, "Respond to repo#1", "fresh context")
    assert f.read_text() == text


def test_triage_skips_deferred_and_uses_the_open_copy(tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text("* Focus\n** DEFERRED Respond to repo#1\n  :PROPERTIES:\n  :ID: d\n  :END:\n"
                 "** TODO Respond to repo#1\n  :PROPERTIES:\n  :ID: t\n  :END:\n")
    triage_utils._append_task_body(f, "Respond to repo#1", "fresh context")
    lines = f.read_text().splitlines()
    assert lines.index("   fresh context") > lines.index("  :ID: t")


# --- G9 / G10: ledger dismissals ----------------------------------------------

@pytest.fixture
def ledger_space(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "test")
    from ledger.log import EventLog
    space = tmp_path / "0-testspace"
    (space / "org").mkdir(parents=True)
    log = EventLog(space, "test")
    for nid in ("id-1", "id-2"):
        log.append("item.create", {"id": nid, "title": "x", "state": "TODO"})
    return space


def _item(space, nid):
    from ledger.fold import fold
    from ledger.log import read_events
    return fold(read_events(space)).items.get(nid)


def _dismissals(space):
    from ledger.log import read_events
    return [e for e in read_events(space) if e.type == "item.dismiss"]


DUP = ("* TODO x\n:PROPERTIES:\n:ID: id-1\n:END:\n"
       "* TODO x\n:PROPERTIES:\n:ID: id-2\n:END:\n")


def test_dedup_apply_dismisses_the_dropped_id_as_housekeeping(ledger_space):
    f = ledger_space / "org" / "inbox.org"
    f.write_text(DUP)
    org_dedup_within_file.dedup(f, apply=True)
    assert "id-2" not in f.read_text()
    item = _item(ledger_space, "id-2")
    assert item.status == "dismissed" and item.closed_kind == "housekeeping"
    assert "id-1" in (item.closed_reason or "")
    assert _item(ledger_space, "id-1").status != "dismissed"


def test_dedup_dry_run_touches_no_ledger(ledger_space):
    f = ledger_space / "org" / "inbox.org"
    f.write_text(DUP)
    org_dedup_within_file.dedup(f, apply=False)
    assert f.read_text() == DUP and not _dismissals(ledger_space)


def test_dedup_without_a_ledger_emits_nothing(tmp_path):
    space = tmp_path / "0-noledger"
    (space / "org").mkdir(parents=True)
    f = space / "org" / "inbox.org"
    f.write_text(DUP)
    org_dedup_within_file.dedup(f, apply=True)
    assert "id-2" not in f.read_text()
    assert not (space / ".datacore").exists()


CONFLICT = ("* TODO x\n:PROPERTIES:\n" + "<" * 7 + " HEAD\n:ID: id-1\n" + "=" * 7 + "\n:ID: id-2\n"
            "" + ">" * 7 + " upstream\n:END:\n")


def test_resolve_dismisses_the_discarded_created_id(ledger_space):
    f = ledger_space / "org" / "inbox.org"
    f.write_text(CONFLICT)
    ok, _ = org_resolve_id_conflicts.resolve(f, apply=True, keep="other")
    assert ok and ":ID: id-2" in f.read_text() and "id-1" not in f.read_text()
    item = _item(ledger_space, "id-1")
    assert item.status == "dismissed" and item.closed_kind == "housekeeping"
    assert "id-2" in (item.closed_reason or "")


def test_resolve_does_not_dismiss_an_id_the_ledger_never_created(ledger_space):
    f = ledger_space / "org" / "inbox.org"
    f.write_text(CONFLICT.replace("id-1", "id-unknown"))
    ok, _ = org_resolve_id_conflicts.resolve(f, apply=True, keep="other")
    assert ok and not _dismissals(ledger_space)


def test_resolve_check_mode_touches_no_ledger(ledger_space):
    f = ledger_space / "org" / "inbox.org"
    f.write_text(CONFLICT)
    org_resolve_id_conflicts.resolve(f, apply=False, keep="other")
    assert not _dismissals(ledger_space)


# --- G12: org_union_merge creates the missing heading -------------------------

def _parent_text(text, needle):
    """Heading text (no stars, no state) of the nearest shallower heading
    before the heading containing `needle`."""
    stack = []
    for _, block in split(text)[1]:
        head = block.splitlines()[0]
        level = len(head) - len(head.lstrip("*"))
        while stack and stack[-1][0] >= level:
            stack.pop()
        if needle in head:
            return stack[-1][1] if stack else None
        words = head.lstrip("*").split()
        if words and words[0] in ("TODO", "NEXT", "WAITING", "REVIEW", "DONE",
                                  "DEFERRED", "CANCELLED"):
            words = words[1:]
        stack.append((level, " ".join(words)))
    raise AssertionError(needle)


def test_union_skip_level_item_gets_a_new_heading_not_an_unrelated_one():
    ours = "* P\n** y\n"
    theirs = "* P\n*** TODO x\n"
    merged, stats = reconcile(ours, theirs)
    assert _parent_text(merged, "TODO x") == "P"     # was "y"
    assert merged == "* P\n** y\n** P\n*** TODO x\n"
    assert stats["headings_created"] == 1


def test_union_skip_level_siblings_share_one_new_heading():
    merged, stats = reconcile("* P\n** y\n", "* P\n*** TODO a\n*** TODO b\n")
    assert merged == "* P\n** y\n** P\n*** TODO a\n*** TODO b\n"
    assert stats["headings_created"] == 1


def test_union_skip_level_with_nothing_in_the_way_matches_theirs():
    merged, stats = reconcile("* P\n", "* P\n*** TODO x\n")
    assert merged == "* P\n*** TODO x\n" and not stats["headings_created"]


def test_union_new_heading_carries_no_state_and_no_id():
    ours = "* TODO P\n:PROPERTIES:\n:ID: p\n:END:\n** y\n"
    theirs = "* TODO P\n:PROPERTIES:\n:ID: p\n:END:\n*** TODO x\n"
    merged, _ = reconcile(ours, theirs)
    assert merged.count(":ID: p") == 1
    assert "\n** P\n*** TODO x\n" in merged


def test_union_anchor_deeper_than_the_item_rebuilds_theirs_chain_at_eof():
    """A recorded closure made ours' copy of the parent win at a DEEPER level
    than theirs has it, so the item cannot sit under that copy at all."""
    ours = ("* Q\n** R\n*** DONE P\nCLOSED: [2026-09-20 Sun 10:00]\n"
            ":PROPERTIES:\n:ID: p\n:END:\n")
    theirs = "* TODO P\n:PROPERTIES:\n:ID: p\n:END:\n** TODO x\n"
    merged, stats = reconcile(ours, theirs)
    assert _parent_text(merged, "TODO x") == "P"     # was "R"
    assert merged.endswith(":END:\n* P\n** TODO x\n")
    assert merged.count(":ID: p") == 1
    assert stats["headings_created"] == 1


def _parents_by_text(text):
    """[(heading text, parent heading text)] in file order, state stripped."""
    out, stack = [], []
    for _, block in split(text)[1]:
        head = block.splitlines()[0]
        level = len(head) - len(head.lstrip("*"))
        words = head.lstrip("*").split()
        if words and words[0] in ("TODO", "DONE"):
            words = words[1:]
        while stack and stack[-1][0] >= level:
            stack.pop()
        out.append((" ".join(words), stack[-1][1] if stack else None))
        stack.append((level, " ".join(words)))
    return out


@pytest.mark.parametrize("seed", range(300))
def test_union_every_theirs_only_item_is_under_its_parents_text_randomised(seed):
    """The Lean theorem `placeTheirs_parent_text`, checked over random outlines
    WITH level skips (the old precondition is gone): every theirs-only item's
    parent in the merge has the text of its parent in theirs, and no block of
    either side is lost."""
    import random
    rng = random.Random(seed)

    def item(level, iid):
        return f"{'*' * level} TODO {iid}\n:PROPERTIES:\n:ID: {iid}\n:END:\n"

    def outline(ids, prefix, fixed=None):
        parts, level = [], 0
        for iid in ids:
            if fixed and iid in fixed:
                parts.append(fixed[iid])
                level = len(fixed[iid]) - len(fixed[iid].lstrip("*"))
                continue
            level = rng.randint(1, min(level + 2, 4))       # may skip a level
            if rng.random() < 0.25:
                parts.append(f"{'*' * rng.randint(1, level)} {prefix}{rng.randint(0, 2)}\n")
            parts.append(item(level, iid))
        return "".join(parts)

    shared = [f"s{i}" for i in range(rng.randint(0, 4))]
    ours_ids = shared + [f"o{i}" for i in range(rng.randint(0, 4))]
    theirs_ids = shared + [f"t{i}" for i in range(rng.randint(0, 5))]
    rng.shuffle(ours_ids)
    rng.shuffle(theirs_ids)
    ours = outline(ours_ids, "u")
    body = {i: b for i, b in split(ours)[1] if i}
    theirs = outline(theirs_ids, "v", fixed=body)
    try:
        merged, _ = reconcile(ours, theirs)
    except ValueError:
        return
    out_blocks = [b for _, b in split(merged)[1]]
    for side in (ours, theirs):
        blocks = [b for _, b in split(side)[1]]
        for blk in blocks:
            assert out_blocks.count(blk) >= blocks.count(blk)
    # Items with a parent in theirs. A parentless item deeper than level 1
    # (theirs' file opens with `** x`) has no parent text to keep; since Q14
    # it goes under a created neutral heading (test_followups_org.py).
    want = {t: p for t, p in _parents_by_text(theirs) if t.startswith("t") and p}
    got = {t: p for t, p in _parents_by_text(merged) if t in want}
    assert got == want, (theirs, merged)
