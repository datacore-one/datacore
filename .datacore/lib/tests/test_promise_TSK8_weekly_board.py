"""TSK-8: The weekly review puts every overdue, stale, waiting and
awaiting-review item on one decision board, and applies my saved choices.

Kind: deterministic. The real gtd_decision_board build + apply on two tmp
spaces (one board across both), with the owner's saved choices written the
way the page's Save writes them.

Seeded failure: in two spaces, tasks that slipped a scheduled date or a
deadline, a NEXT untouched for eight weeks, WAITING and REVIEW items, next to
a fresh TODO and a finished task. Every one of the first kind must be on the
one board exactly once, the other two must not, and each saved choice must
land on its task (drop, next, done, accept, defer). Verified red by making
classify() skip WAITING items.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))

import gtd_decision_board as board  # noqa: E402

TODAY = "2026-09-26"
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"


def _t(state, title, tid, created="2026-09-01", plan=""):
    return (f"* {state} {title}\n" + (plan + "\n" if plan else "")
            + f":PROPERTIES:\n:ID: {tid}\n:CREATED: [{created}]\n:END:\n")


A = HEADER + "".join([
    _t("NEXT", "Send the invoice", "a-slipped-sched", plan="SCHEDULED: <2026-09-20 Sun>"),
    _t("TODO", "File the VAT return", "a-slipped-deadline", plan="DEADLINE: <2026-09-25 Fri>"),
    _t("NEXT", "Tidy the garage", "a-stale", created="2026-08-01"),
    _t("WAITING", "Quote from the plumber", "a-waiting", created="2026-09-20"),
    _t("REVIEW", "Agent draft of the newsletter", "a-review", created="2026-09-24"),
    _t("TODO", "Fresh idea from yesterday", "a-fresh", created="2026-09-25"),
    _t("DONE", "Old finished thing", "a-done", created="2026-06-01"),
])
B = HEADER + "".join([
    _t("WAITING", "Legal sign-off on the contract", "b-waiting", created="2026-09-10"),
    _t("REVIEW", "PR for the sync fix", "b-review", created="2026-09-22"),
    _t("TODO", "Renew the certificate", "b-slipped", plan="SCHEDULED: <2026-09-21 Mon>"),
])
MUST_ASK = {"a-slipped-sched", "a-slipped-deadline", "a-stale", "a-waiting", "a-review",
            "b-waiting", "b-review", "b-slipped"}
CHOICES = {"a-slipped-sched": ("drop", ""), "a-stale": ("next", ""), "a-waiting": ("done", ""),
           "a-review": ("accept", ""), "b-slipped": ("defer", "2026-10-10")}


@pytest.fixture
def spaces(tmp_path, monkeypatch):
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ACTOR", "tsk8-eval")
    files = []
    for space, text in (("0-personal", A), ("2-datacore", B)):
        f = tmp_path / space / "org" / "next_actions.org"
        f.parent.mkdir(parents=True)
        f.write_text(text, encoding="utf-8")
        files.append(f.resolve())
    out = state / "decision-boards" / "w39-weekly-review.html"
    args = argparse.Namespace(
        today=TODAY, slug="w39-weekly-review", title="W39 Weekly Review", redact=[], overrides=None,
        week=None, prefill=None, files=[str(f) for f in files], projects=[], states=None, min_age=None,
        eyebrow=None, h1=None, lede=None, as_of=None, out=str(out))
    board.build(args)
    return files, out


def _rows(out: Path) -> list[dict]:
    data, _ = board._board_rows(out)
    return [r for s in data["sections"] for r in s["rows"]]


def test_every_item_that_needs_a_call_is_on_one_board_once(spaces):
    _, out = spaces
    ids = [(r.get("apply") or {}).get("orgId") for r in _rows(out)]
    missing = MUST_ASK - set(ids)
    assert not missing, f"not on the weekly board: {sorted(missing)}"
    assert len(ids) == len(set(ids)), f"an item is on the board twice: {ids}"
    assert "a-fresh" not in ids and "a-done" not in ids


def test_saved_choices_are_applied_to_the_tasks(spaces):
    files, out = spaces
    data, _ = board._board_rows(out)
    by_org = {(r.get("apply") or {}).get("orgId"): r["id"] for r in _rows(out)}
    saved = out.with_name("w39-weekly-review.decisions.json")
    saved.write_text(json.dumps({"board": data["meta"]["slug"], "build": data["meta"]["build"],
                                 "decisions": {by_org[o]: {"choice": c, "note": n}
                                               for o, (c, n) in CHOICES.items()}}))
    saved.chmod(0o600)
    board.apply_cmd(argparse.Namespace(board=str(out), decisions=str(saved), today=TODAY,
                                       someday_parent=None, dry_run=False, show=25))
    text = "".join(f.read_text(encoding="utf-8") for f in files)
    state = {}
    for block in re.split(r"(?m)^(?=\* )", text):
        m = re.search(r"(?m)^:ID:\s+(\S+)", block)
        if m:
            state[m.group(1)] = (block.split()[1], block)
    assert state["a-slipped-sched"][0] == "CANCELLED"
    assert state["a-stale"][0] == "NEXT"
    assert state["a-waiting"][0] == "DONE"
    assert state["a-review"][0] == "DONE"
    assert state["b-slipped"][0] == "TODO" and "SCHEDULED: <2026-10-10" in state["b-slipped"][1]
    for oid in CHOICES:
        assert f":LAST_REVIEWED: {TODAY}" in state[oid][1], f"{oid} was not stamped as reviewed"
    assert state["b-waiting"][0] == "WAITING", "an undecided row was changed"
