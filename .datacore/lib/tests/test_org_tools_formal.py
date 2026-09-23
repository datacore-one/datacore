"""Counterexamples found by the Lean model of the org tools (2026-09-23).

Each test replays one counterexample from `specs/datacore-lean/DatacoreSpec/
OrgTools.lean` against the real Python, and pins the fixed behaviour. The
findings are written up in `specs/datacore-lean/findings/org-tools.md`.
"""
from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

import dedup  # noqa: E402
import inbox_cleanup  # noqa: E402
import org_dedup_within_file  # noqa: E402
import org_resolve_id_conflicts  # noqa: E402
import triage_utils  # noqa: E402
from org_union_merge import reconcile, split  # noqa: E402


# --- org_union_merge -------------------------------------------------------

def _parent_ids(text):
    """id -> id of the nearest preceding heading at a shallower level."""
    out, stack = {}, []
    for iid, block in split(text)[1]:
        level = len(block) - len(block.lstrip("*"))
        while stack and stack[-1][0] >= level:
            stack.pop()
        if iid:
            out[iid] = stack[-1][1] if stack else None
        stack.append((level, iid))
    return out


def _item(level, iid, title):
    return f"{'*' * level} TODO {title}\n:PROPERTIES:\n:ID: {iid}\n:END:\n"


def test_union_keeps_a_theirs_block_that_is_only_a_substring_of_ours():
    """`block not in a` was a substring test: "** TODO call mom" is inside
    "*** TODO call mom", so theirs' entry was dropped."""
    ours = "* Inbox\n*** TODO call mom\n"
    theirs = "* Inbox\n** TODO call mom\n"
    merged, _ = reconcile(ours, theirs)
    assert "\n** TODO call mom\n" in merged
    assert "\n*** TODO call mom\n" in merged


def test_union_keeps_a_theirs_only_child_under_its_parent():
    """A theirs-only child used to be appended at EOF, under whatever heading
    was last (here Q instead of P)."""
    ours = _item(1, "p", "P") + _item(1, "q", "Q")
    theirs = _item(1, "p", "P") + _item(2, "c", "child") + _item(1, "q", "Q")
    merged, _ = reconcile(ours, theirs)
    assert _parent_ids(merged)["c"] == "p"
    assert _parent_ids(merged)["q"] is None


def test_union_places_a_child_after_ours_children_and_keeps_theirs_order():
    ours = _item(1, "p", "P") + _item(2, "o", "ours child") + _item(3, "g", "grand") \
        + _item(1, "q", "Q")
    theirs = _item(1, "p", "P") + _item(2, "t1", "t1") + _item(3, "t1a", "t1a") \
        + _item(2, "t2", "t2") + _item(1, "q", "Q")
    merged, _ = reconcile(ours, theirs)
    par = _parent_ids(merged)
    assert par == {"p": None, "o": "p", "g": "o", "t1": "p", "t1a": "t1",
                   "t2": "p", "q": None}
    order = [i for i, _ in split(merged)[1]]
    assert order == ["p", "o", "g", "t1", "t1a", "t2", "q"]


def test_union_children_of_an_unidentified_section_stay_in_it():
    ours = "* Inbox\n" + _item(2, "a", "a") + "* Later\n"
    theirs = "* Inbox\n" + _item(2, "b", "b") + "* Later\n"
    merged, _ = reconcile(ours, theirs)
    assert merged == "* Inbox\n" + _item(2, "a", "a") + _item(2, "b", "b") + "* Later\n"


def test_union_whitespace_only_difference_does_not_duplicate_a_section():
    ours = "* Inbox\n\n" + _item(2, "a", "a")
    theirs = "* Inbox\n" + _item(2, "b", "b")
    merged, _ = reconcile(ours, theirs)
    assert merged.count("* Inbox") == 1


def test_union_unidentified_blocks_are_a_multiset_union():
    ours = "* x\n"
    theirs = "* x\n* x\n"
    merged, _ = reconcile(ours, theirs)
    assert merged.count("* x\n") == 2


def _random_file(rng, ids, prefix):
    lines, level = [], 0
    for iid in ids:
        level = rng.randint(1, min(level + 1, 3))
        if rng.random() < 0.25:
            lines.append(f"{'*' * level} {prefix}{rng.randint(0, 2)}\n")
        lines.append(_item(level, iid, iid))
    return "".join(lines)


@pytest.mark.parametrize("seed", range(200))
def test_union_loses_nothing_and_keeps_parents_randomised(seed):
    """Property check mirroring the Lean theorems: every block of either side is
    in the result, and a theirs-only item keeps the parent it had in theirs
    whenever that parent is an identified item."""
    rng = random.Random(seed)
    shared = [f"s{i}" for i in range(rng.randint(0, 4))]
    ours_ids = shared + [f"o{i}" for i in range(rng.randint(0, 4))]
    theirs_ids = shared + [f"t{i}" for i in range(rng.randint(0, 4))]
    rng.shuffle(ours_ids)
    rng.shuffle(theirs_ids)
    # shared items must agree byte for byte (differences are refused anyway)
    body = {}
    ours = _random_file(rng, ours_ids, "u")
    for iid, blk in split(ours)[1]:
        if iid:
            body[iid] = blk
    parts, level = [], 0
    for i in theirs_ids:
        if i in body and i in shared:
            blk = body[i]
            level = len(blk) - len(blk.lstrip("*"))
        else:
            level = rng.randint(1, min(level + 1, 3))
            blk = _item(level, i, i)
        parts.append(blk)
    theirs = "".join(parts)
    try:
        merged, _ = reconcile(ours, theirs)
    except ValueError:
        return  # a refusal is allowed; silently losing an item is not
    out_blocks = [b for _, b in split(merged)[1]]
    for side in (ours, theirs):
        for blk in [b for _, b in split(side)[1]]:
            assert out_blocks.count(blk) >= [b for _, b in split(side)[1]].count(blk)
    pt, pm = _parent_ids(theirs), _parent_ids(merged)
    lv = {i: len(b) - len(b.lstrip("*")) for i, b in split(theirs)[1]}
    for iid in theirs_ids:
        # Stated precondition (see findings): the child sits exactly one level
        # below its parent. A level gap has no position that keeps every parent.
        if iid.startswith("t") and pt[iid] is not None and lv[iid] == lv[pt[iid]] + 1:
            assert pm[iid] == pt[iid], (iid, theirs, merged)


# --- inbox_cleanup ---------------------------------------------------------

def test_inbox_cleanup_never_archives_deferred():
    """DIP-0009: DEFERRED is closed-but-wakeable. Archived out of inbox.org it
    can never be woken by the nightly sweep."""
    text = ("* Inbox\n** DEFERRED wake me\nSCHEDULED: <2026-10-01 Thu>\n"
            "* DONE x\n** DEFERRED child\n")
    out, arch, stats = inbox_cleanup.clean(text, "2026-09-23")
    assert "** DEFERRED wake me" in out
    assert "DEFERRED child" in out
    assert "DEFERRED" not in (arch or "")


def _headings(text):
    return sorted(l.lstrip("*").strip() for l in (text or "").split("\n")
                  if l.startswith("*") and l.lstrip("*").startswith(" "))


_STATES = ["TODO", "NEXT", "WAITING", "REVIEW", "DONE", "CANCELLED", "DEFERRED", ""]


def _random_inbox(rng):
    lines, level = ["#+TITLE: x", ""], 0
    if rng.random() < .7:
        lines.append("* Inbox")
        level = 1
    for n in range(rng.randint(0, 10)):
        level = rng.randint(1, min(level + 1, 4))
        st = rng.choice(_STATES)
        lines.append(f"{'*' * level} {st + ' ' if st else ''}item{n}")
        if rng.random() < .5:
            lines.append(f"body{n}")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("seed", range(300))
def test_inbox_cleanup_properties_randomised(seed):
    """Mirrors the Lean theorems: nothing live is archived, every entry lands in
    exactly one of the two outputs, and a second run changes nothing."""
    text = _random_inbox(random.Random(seed))
    out, arch, _ = inbox_cleanup.clean(text, "2026-09-23")
    for h in _headings(arch):
        if h.startswith("Archived (processed"):
            continue
        assert h.split(" ")[0] in ("DONE", "CANCELLED") or not h.split(" ")[0] in _STATES \
            or h.split(" ")[0] == "", h
        assert not h.startswith(("TODO", "NEXT", "WAITING", "REVIEW", "DEFERRED")), h
    got = [h for h in _headings(out) + _headings(arch)
           if h not in ("Inbox",) and not h.startswith("Archived (processed")]
    want = [h for h in _headings(text) if h != "Inbox"]
    assert sorted(got) == sorted(want)
    out2, arch2, _ = inbox_cleanup.clean(out, "2026-09-23")
    assert out2 == out and arch2 is None


# --- org_dedup_within_file -------------------------------------------------

def test_dedup_within_file_names_the_ids_it_drops(tmp_path, capsys):
    """Copies with DIFFERENT ids are treated as duplicates on purpose (the
    2026-08-15 incident), but the dropped id is live in the ledger, so the
    report must name it for reconciliation."""
    f = tmp_path / "inbox.org"
    f.write_text("* TODO x\n:PROPERTIES:\n:ID: id-1\n:END:\n"
                 "* TODO x\n:PROPERTIES:\n:ID: id-2\n:END:\n")
    removed, _, _ = org_dedup_within_file.dedup(f, apply=False)
    assert removed == 1
    assert "id-2" in capsys.readouterr().out


# --- org_resolve_id_conflicts ---------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=False)


def _conflicted_repo(tmp_path, mode):
    """A repo where 'upstream' and 'local' minted different ids for one
    heading, stopped in a merge or a rebase conflict."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    f = repo / "inbox.org"
    f.write_text("* TODO task\n:PROPERTIES:\n:END:\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "upstream")
    f.write_text("* TODO task\n:PROPERTIES:\n:ID: UPSTREAM-ID\n:END:\n")
    _git(repo, "commit", "-q", "-am", "upstream id")
    _git(repo, "checkout", "-q", "main")
    f.write_text("* TODO task\n:PROPERTIES:\n:ID: LOCAL-ID\n:END:\n")
    _git(repo, "commit", "-q", "-am", "local id")
    if mode == "merge":
        _git(repo, "merge", "upstream")
    else:
        _git(repo, "rebase", "upstream")
    assert "<<<<<<<" in f.read_text()
    return f


@pytest.mark.parametrize("mode", ["merge", "rebase"])
def test_resolve_id_conflicts_keeps_the_upstream_id(tmp_path, mode):
    """HEAD is upstream only during a rebase. git_fleet_sync MERGES (DIP-0046),
    where HEAD is the local side — the old code kept the local id there."""
    f = _conflicted_repo(tmp_path, mode)
    ok, msg = org_resolve_id_conflicts.resolve(f, apply=True)
    assert ok, msg
    text = f.read_text()
    assert "UPSTREAM-ID" in text and "LOCAL-ID" not in text


def test_resolve_id_conflicts_refuses_when_it_cannot_tell_the_sides(tmp_path):
    f = tmp_path / "inbox.org"
    f.write_text("* TODO task\n:PROPERTIES:\n<<<<<<< HEAD\n:ID: A\n=======\n:ID: B\n"
                 ">>>>>>> other\n:END:\n")
    before = f.read_text()
    ok, _ = org_resolve_id_conflicts.resolve(f, apply=True)
    assert not ok and f.read_text() == before
    ok, _ = org_resolve_id_conflicts.resolve(f, apply=True, keep="other")
    assert ok and ":ID: B" in f.read_text() and ":ID: A" not in f.read_text()


# --- triage_utils._append_task_body ---------------------------------------

TWO_TASKS = """* Focus
** TODO Respond to repo#1 — first
** TODO Respond to repo#2 — second
  :PROPERTIES:
  :ID: two
  :END:
"""


def test_append_body_never_writes_into_another_tasks_drawer(tmp_path):
    """The target has no drawer; the first `:END:` after it belongs to the NEXT
    task, and the body landed there."""
    f = tmp_path / "next_actions.org"
    f.write_text(TWO_TASKS)
    triage_utils._append_task_body(f, "Respond to repo#1", "context for one")
    text = f.read_text()
    second = text.index("** TODO Respond to repo#2")
    assert "context for one" not in text[second:]


SHARED_LINE = """* Focus
** TODO Respond to repo#1 — first
  :PROPERTIES:
  :ID: one
  :END:
   Source: github
** TODO Respond to repo#2 — second
  :PROPERTIES:
  :ID: two
  :END:
"""


def test_append_body_duplicate_check_is_scoped_to_the_target_task(tmp_path):
    """The 'already recorded' test searched the WHOLE file, so a first line that
    another task already carries suppressed the write for this one."""
    f = tmp_path / "next_actions.org"
    f.write_text(SHARED_LINE)
    triage_utils._append_task_body(f, "Respond to repo#2", "Source: github\nissue two")
    text = f.read_text()
    assert "issue two" in text[text.index("repo#2"):]
    for _ in range(3):
        triage_utils._append_task_body(f, "Respond to repo#2", "Source: github\nissue two")
    assert f.read_text().count("issue two") == 1


# --- dedup.deduplicate -----------------------------------------------------

def test_deduplicate_keeps_an_item_whose_only_match_was_dropped():
    """Jaccard similarity is not transitive: A~B, B~C, A!~C. Keep-first keeps A,
    drops B as A's duplicate, and must keep C, which duplicates nothing kept."""
    items = [{"title": "a b c d"}, {"title": "a b c d e"}, {"title": "b c d e f"}]
    assert dedup.title_similarity("a b c d", "b c d e f") < 0.6
    out = dedup.deduplicate(items, threshold=0.6)
    assert out == [items[0], items[2]]


def test_deduplicate_exact_content_is_still_transitive():
    items = [{"title": "x", "content": "same"}, {"title": "y", "content": "same"},
             {"title": "z", "content": "same"}]
    assert dedup.deduplicate(items) == [items[0]]


@pytest.mark.parametrize("seed", range(100))
def test_deduplicate_greedy_spec_randomised(seed):
    rng = random.Random(seed)
    words = "a b c d e f".split()
    items = [{"title": " ".join(rng.sample(words, rng.randint(1, 4)))}
             for _ in range(rng.randint(0, 8))]
    out = dedup.deduplicate(items, threshold=0.5)
    kept = [i for i, it in enumerate(items) if any(it is o for o in out)]
    dup = lambda i, j: dedup.title_similarity(items[i]["title"], items[j]["title"]) >= 0.5  # noqa: E731
    for n, i in enumerate(kept):
        assert not any(dup(k, i) for k in kept[:n])
    for j in range(len(items)):
        if j not in kept:
            assert any(k < j and dup(k, j) for k in kept)
