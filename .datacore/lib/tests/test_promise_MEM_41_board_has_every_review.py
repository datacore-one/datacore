"""MEM-41: My daily decision board includes every task waiting for my review,
across all spaces, and each row lets me say "already done".

Kind: deterministic. The real board builder (gtd_decision_board.build) over a
tmp Data tree with REVIEW tasks in three spaces (one of them looked at on a
board three days ago), plus ordinary open tasks:
  * every REVIEW task of every space is a row (ENG-2026-09-21-059), including
    one reviewed earlier this week -- it is still waiting for me;
  * every task row offers "done" / "Already done" (ENG-2026-09-11-023),
    REVIEW rows included.

Seeded failure: a board built from one space's files only, or REVIEW rows
without the "done" option (today: "REVIEW rows already have Accept").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

HDR = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"
TODAY = "2026-09-26"


def _task(state, title, tid, extra=""):
    return (f"** {state} {title}\n:PROPERTIES:\n:ID: {tid}\n:CREATED: [2026-08-01]\n{extra}:END:\n")


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = (tmp_path / "Data").resolve()
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    files = []
    specs = {
        "0-personal": [("REVIEW", "Approve the trip budget", "r-personal", ""),
                       ("NEXT", "Renew the passport", "n-personal", "")],
        "1-team": [("REVIEW", "Review the grant draft", "r-team", ""),
                   ("REVIEW", "Review the landing copy", "r-team-seen", ":LAST_REVIEWED: 2026-09-23\n")],
        "2-dev": [("REVIEW", "Check the migration PR output", "r-dev", ":NIGHTSHIFT_SCORE: 0.9\n"),
                  ("TODO", "Old backlog item", "t-dev", "")],
    }
    for space, tasks in specs.items():
        org = root / space / "org"
        org.mkdir(parents=True)
        f = org / "next_actions.org"
        f.write_text(HDR + "* Work\n" + "".join(_task(*t) for t in tasks), encoding="utf-8")
        files.append(str(f))
    return root, files, tmp_path


def _build(files, out: Path):
    import gtd_decision_board as B
    args = argparse.Namespace(files=files, projects=None, today=TODAY, slug="daily", title="Daily board",
                              week=None, overrides=None, redact=None, prefill=None, states=None,
                              min_age=None, h1=None, eyebrow=None, lede=None, as_of="x", out=str(out))
    B.build(args)
    html = out.read_text(encoding="utf-8")
    start = html.index('id="data">') + len('id="data">')
    data = json.loads(html[start:html.index("</script>", start)])
    return [r for s in data["sections"] for r in s["rows"] if r.get("apply")]


def test_every_review_task_of_every_space_is_on_the_board(data):
    root, files, tmp = data
    out = tmp / "boards"
    out.mkdir(mode=0o700)
    rows = _build(files, out / "daily.html")
    ids = {r["apply"]["orgId"] for r in rows}
    want = {"r-personal", "r-team", "r-team-seen", "r-dev"}
    assert want <= ids, f"REVIEW tasks missing from the board: {sorted(want - ids)}"


def test_every_row_offers_already_done(data):
    root, files, tmp = data
    out = tmp / "boards"
    out.mkdir(mode=0o700)
    rows = _build(files, out / "daily.html")
    assert rows, "the board has no task rows"
    lacking = [f"{r['apply']['orgId']} ({r['apply']['state']})" for r in rows
               if "done" not in {o["value"] for o in r["options"]}]
    assert not lacking, f"rows without an 'Already done' option: {lacking}"
