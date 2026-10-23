"""The morning review board (org task cedc45c3).

Owner: "for reviews, there should be a decision board generated in the morning."

DONE_WHEN, one test (or more) per clause:
  * a morning job writes a local decision board of every item awaiting the
    owner's review (decision-board format, local, never published);
  * a rules file lets the owner mark review types that may pass on green tests
    plus a second model family;
  * with the shipped default nothing skips the owner;
  * release, security, money, credentials and public posts can never pass
    without the owner;
  * saved answers apply through the existing apply path (gtd_decision_board apply).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import review_board as RB  # noqa: E402
import gtd_decision_board as B  # noqa: E402

HDR = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"
TODAY = "2026-10-23"
GREEN = (":TESTS: green, 41 passed\n:MODEL: claude-opus\n"
         ":SECOND_REVIEW: pass\n:SECOND_REVIEW_MODEL: gpt-5\n")


def _task(state, title, tid, extra="", tags=""):
    head = f"** {state} {title}" + (f" {tags}" if tags else "")
    return f"{head}\n:PROPERTIES:\n:ID: {tid}\n:CREATED: [2026-10-20]\n{extra}:END:\n"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = (tmp_path / "Data").resolve()
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ROOT", str(root))

    def write(space, tasks, name="next_actions.org"):
        org = root / space / "org"
        org.mkdir(parents=True, exist_ok=True)
        f = org / name
        f.write_text(HDR + "* Work\n" + "".join(_task(*t) for t in tasks), encoding="utf-8")
        return f
    return root, state, write


def _rows(path: Path):
    html = path.read_text(encoding="utf-8")
    start = html.index('id="data">') + len('id="data">')
    data = json.loads(html[start:html.index("</script>", start)])
    return data, {r["apply"]["orgId"]: (s["key"], r) for s in data["sections"]
                  for r in s["rows"] if r.get("apply")}


def _run(root, rules=None, today=TODAY):
    return RB.run(root=root, rules_path=rules, today=today, as_of="fixture")


# --- the board: every review item, local, with review options and evidence ---

def test_board_has_every_review_item_across_spaces_and_only_those(tree):
    root, state, write = tree
    write("0-personal", [("REVIEW", "Check the trip plan", "r-a"), ("NEXT", "Renew passport", "n-a")])
    write("5-team", [("REVIEW", "Nightshift research brief", "r-b", ":NIGHTSHIFT_OUTPUT: 0-inbox/brief.md\n",
                      ":AI:research:")])
    write("5-team", [("REVIEW", "Queued output", "r-c")], name="nightshift.org")
    result = _run(root)
    data, rows = _rows(Path(result["out"]))
    assert set(rows) == {"r-a", "r-b", "r-c"}
    assert result["items"] == 3


def test_board_stays_local_and_private(tree):
    root, state, write = tree
    write("0-personal", [("REVIEW", "Check it", "r-a")])
    out = Path(_run(root)["out"])
    assert out.parent == state / "decision-boards"
    assert out.name == f"{TODAY}-morning-review.html"
    assert out.stat().st_mode & 0o777 == 0o600
    text = out.read_text()
    assert "default-src 'none'" in text and "claude.ai" not in text


def test_rows_offer_accept_rework_drop(tree):
    root, state, write = tree
    write("0-personal", [("REVIEW", "Check it", "r-a")])
    _, rows = _rows(Path(_run(root)["out"]))
    opts = {o["value"]: o["label"] for o in rows["r-a"][1]["options"]}
    assert {"accept", "next", "drop"} <= set(opts)
    assert opts["next"].lower().startswith("rework")
    assert "someday" not in opts


def test_rows_carry_evidence_links(tree):
    root, state, write = tree
    extra = (":PR: https://github.com/example/repo/pull/12\n"
             ":NIGHTSHIFT_OUTPUT: 0-inbox/report.md\n:TESTS: green, 41 passed\n")
    write("0-personal", [("REVIEW", "Merge-ready fix", "r-a", extra)])
    _, rows = _rows(Path(_run(root)["out"]))
    row = rows["r-a"][1]
    assert {"text": "PR", "url": "https://github.com/example/repo/pull/12"} in row["links"]
    meta = " ".join(row["meta"])
    assert "0-inbox/report.md" in meta and "41 passed" in meta


# --- rules file: default and example skip nothing ---

def test_default_without_rules_file_nothing_skips_owner(tree):
    root, state, write = tree
    write("0-personal", [("REVIEW", "Research digest", "r-a", GREEN, ":AI:research:")])
    _, rows = _rows(Path(_run(root, rules=root / "missing.yaml")["out"]))
    assert rows["r-a"][0] == "review"


def test_shipped_example_skips_nothing(tree):
    example = LIB.parent / "config" / "review_rules.local.yaml.example"
    assert example.exists()
    rules = RB.load_rules(example)
    assert rules["auto_pass"] == []
    root, state, write = tree
    write("0-personal", [("REVIEW", "Research digest", "r-a", GREEN, ":AI:research:")])
    _, rows = _rows(Path(_run(root, rules=example)["out"]))
    assert rows["r-a"][0] == "review"


def _rules(tmp, types):
    p = tmp / "rules.yaml"
    p.write_text(yaml.safe_dump({"auto_pass": types}))
    return p


def test_marked_type_passes_only_with_green_tests_and_second_family(tree, tmp_path):
    root, state, write = tree
    rules = _rules(tmp_path, ["research"])
    write("0-personal", [
        ("REVIEW", "Digest with both checks", "ok", GREEN, ":AI:research:"),
        ("REVIEW", "Digest without second review", "no-second",
         ":TESTS: green, 3 passed\n:MODEL: claude-opus\n", ":AI:research:"),
        ("REVIEW", "Digest reviewed by same family", "same-family",
         ":TESTS: green\n:MODEL: claude-opus\n:SECOND_REVIEW: pass\n:SECOND_REVIEW_MODEL: claude-sonnet\n",
         ":AI:research:"),
        ("REVIEW", "Digest with red tests", "red",
         GREEN.replace("green, 41 passed", "red, 2 failed"), ":AI:research:"),
        ("REVIEW", "Content not marked", "other-type", GREEN, ":AI:content:"),
    ])
    result = _run(root, rules=rules)
    _, rows = _rows(Path(result["out"]))
    assert rows["ok"][0] == "autopass"
    for tid in ("no-second", "same-family", "red", "other-type"):
        assert rows[tid][0] == "review", tid


# --- never auto-passed, whatever the rules say ---

PROTECTED = [
    ("release", "Cut the 1.4 release", ""),
    ("security", "Security fix for the token check", ""),
    ("money", "Pay the hosting invoice", ""),
    ("credentials", "Rotate the API key", ""),
    ("public posts", "Publish the launch post on X", ""),
]


@pytest.mark.parametrize("kind,title,_", PROTECTED)
def test_protected_kinds_never_auto_pass(tree, tmp_path, kind, title, _):
    root, state, write = tree
    rules = _rules(tmp_path, ["research"])
    write("0-personal", [("REVIEW", title, "p", GREEN, ":AI:research:")])
    _, rows = _rows(Path(_run(root, rules=rules)["out"]))
    assert rows["p"][0] == "review", f"{kind} passed without the owner"


@pytest.mark.parametrize("typ", ["release", "security", "money", "credentials", "public-post"])
def test_rules_cannot_mark_a_protected_type(tree, tmp_path, typ):
    rules = RB.load_rules(_rules(tmp_path, [typ, "research"]))
    assert rules["auto_pass"] == ["research"]
    assert typ in rules["refused"]
    root, state, write = tree
    write("0-personal", [("REVIEW", "Ordinary item", "p", GREEN + f":REVIEW_TYPE: {typ}\n")])
    _, rows = _rows(Path(_run(root, rules=_rules(tmp_path, [typ]))["out"]))
    assert rows["p"][0] == "review"


# --- saved answers apply through the existing apply path ---

def test_saved_answers_apply_through_gtd_decision_board(tree, monkeypatch):
    root, state, write = tree
    f = write("0-personal", [("REVIEW", "Check it", "r-a"), ("REVIEW", "Rework this", "r-b")])
    out = Path(_run(root)["out"])
    data, rows = _rows(out)
    ids = {tid: r["id"] for tid, (_, r) in rows.items()}
    saved = state / "saved.json"
    saved.write_text(json.dumps({"board": data["meta"]["slug"], "build": data["meta"]["build"],
                                 "decisions": {ids["r-a"]: {"choice": "accept", "note": ""},
                                               ids["r-b"]: {"choice": "next", "note": ""}}}))
    calls = []
    monkeypatch.setattr(B, "_adapter", lambda op: calls.append(op) or {"updated": True})
    B.apply_cmd(argparse.Namespace(board=str(out), decisions=str(saved), today=TODAY,
                                   someday_parent=None, dry_run=False, show=25))
    by_id = {op[op.index("--id") + 1]: op for op in calls}
    assert by_id["r-a"][by_id["r-a"].index("--state") + 1] == "DONE"
    assert by_id["r-b"][by_id["r-b"].index("--state") + 1] == "NEXT"
    assert all(op[op.index("--file") + 1] == str(f.resolve()) for op in calls)


def test_rule_passed_rows_are_prefilled_and_saved_as_applyable_answers(tree, tmp_path, monkeypatch):
    root, state, write = tree
    write("0-personal", [("REVIEW", "Digest", "ok", GREEN, ":AI:research:"),
                         ("REVIEW", "Other", "keep")])
    result = _run(root, rules=_rules(tmp_path, ["research"]))
    data, rows = _rows(Path(result["out"]))
    rid = rows["ok"][1]["id"]
    assert data["prefill"] == {rid: {"choice": "accept", "note": "passed by your review rules"}}
    auto = Path(result["autopass"])
    assert auto.parent == state / "decision-boards" / "answers"
    calls = []
    monkeypatch.setattr(B, "_adapter", lambda op: calls.append(op) or {"updated": True})
    B.apply_cmd(argparse.Namespace(board=result["out"], decisions=str(auto), today=TODAY,
                                   someday_parent=None, dry_run=True, show=25))
    assert calls == []  # dry run plans only; the plan itself is validated against the build


def test_empty_review_queue_still_writes_a_board_and_summary(tree):
    root, state, write = tree
    write("0-personal", [("NEXT", "Nothing to review", "n-a")])
    result = _run(root)
    assert result["items"] == 0
    assert Path(result["out"]).exists()


# --- the morning job ---

def test_morning_job_is_declared_on_the_mac():
    doc = yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())
    jobs = [j for j in doc["jobs"] if j["name"] == "mac-review-board"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["machine"] == "mac" and job["trigger"] == "arrival" and job["every"] == "day"
    assert "review_board.py" in job["cmd"]
    assert any(a["path"].startswith("~/.datacore/state/") for a in job["artifacts"])


def test_summary_line_matches_the_job_contract(tree, capsys):
    root, state, write = tree
    write("0-personal", [("REVIEW", "Check it", "r-a")])
    RB.main(["--root", str(root), "--today", TODAY, "--rules", str(root / "none.yaml")])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    doc = yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())
    job = next(j for j in doc["jobs"] if j["name"] == "mac-review-board")
    import re
    assert re.search(job["artifacts"][0]["arg"], last), last
