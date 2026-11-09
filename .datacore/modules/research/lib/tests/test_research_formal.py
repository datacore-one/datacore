"""Findings from the Lean model DatacoreSpec/Research.lean (2026-09-23).

Each test replays a counterexample the model produced against the real
orchestrator, then pins the fixed behaviour. Fetch and the Claude analysis are
stubbed; nothing touches the network or real org files.
"""
import importlib.util, pathlib, sys
from unittest.mock import MagicMock

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1]
for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    if name not in sys.modules:
        sys.modules[name] = MagicMock()
spec = importlib.util.spec_from_file_location("ro_formal", LIB / "research_orchestrator.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
spec2 = importlib.util.spec_from_file_location("rr_formal", LIB / "research_router.py")
RR = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(RR)


@pytest.fixture
def env(tmp_path, monkeypatch):
    tmp = tmp_path.resolve()
    monkeypatch.setenv("DATACORE_STATE", str(tmp / "state"))
    org = tmp / "research_learning.org"
    monkeypatch.setattr(R, "RESEARCH_ORG", org)
    monkeypatch.setattr(R, "DATA_DIR", tmp)
    monkeypatch.setattr(R, "JOURNAL_DIR", tmp / "journals")
    logs = []
    monkeypatch.setattr(R, "log", logs.append)
    for fn in ("write_zettels",):
        monkeypatch.setattr(R, fn, lambda *a: [])
    monkeypatch.setattr(R, "write_literature_note", lambda d: None)
    monkeypatch.setattr(R, "write_companies", lambda *a: [])
    monkeypatch.setattr(R, "write_people", lambda *a: [])
    monkeypatch.setattr(R, "append_landscape_rows", lambda c: [])
    monkeypatch.setattr(R, "send_telegram_summary", lambda *a: True)
    return org, logs, tmp


def run(monkeypatch, fetch, analyse, limit):
    monkeypatch.setattr(R, "fetch_url", fetch)
    monkeypatch.setattr(R, "process_item", analyse)
    monkeypatch.setattr(sys, "argv", ["x", "--limit", str(limit), "--no-daily-news",
                                      "--no-podcast", "--auto-archive-days", "0"])
    R.main()


def test_analysis_failure_counts_toward_parking_so_the_queue_drains(env, monkeypatch):
    """Lean: old_head_of_line_blocking. An [#A] item whose analysis always
    fails held the only slot forever; the [#C] item behind it never ran."""
    org, logs, _ = env
    org.write_text("* Q\n** TODO [#A] Read: a\n   Link: https://a.test/x\n"
                   "** TODO [#C] Read: b\n   Link: https://b.test/y\n")
    analyse = lambda it, c: None if "a.test" in it["url"] else {"summary": "s"}
    for _ in range(R.MAX_ANALYSIS_ATTEMPTS):
        run(monkeypatch, lambda u: "content", analyse, limit=1)
    text = org.read_text()
    assert "** WAITING [#A] Read: a" in text
    assert f":ANALYSIS_ATTEMPTS: {R.MAX_ANALYSIS_ATTEMPTS}" in text
    assert "analysis failed" in text
    run(monkeypatch, lambda u: "content", analyse, limit=1)
    assert "** DONE [#C] Read: b" in org.read_text()


def test_duplicate_titles_mark_the_item_that_was_processed(env, monkeypatch):
    """Lean: old_duplicate_title_misdirects. The first copy failed to fetch,
    the second succeeded, and the FIRST was marked DONE."""
    org, _, _ = env
    org.write_text("* Q\n** TODO Read: same\n   Link: https://bad.test/1\n"
                   "** TODO Read: same\n   Link: https://ok.test/2\n")
    run(monkeypatch, lambda u: None if "bad" in u else "c", lambda it, c: {"summary": "s"}, limit=2)
    lines = org.read_text().split("\n")
    first = lines.index("** TODO Read: same")
    # D9: the fetch counter now sits in a drawer under the heading, so the
    # item's own section is wider than the old 3-line window.
    assert "https://bad.test/1" in "\n".join(lines[first:first + 6])
    done = lines.index("** DONE Read: same")
    assert done > first and "https://ok.test/2" in "\n".join(lines[done:done + 4])


def test_heading_that_is_a_prefix_of_another_does_not_mark_the_other(env, monkeypatch):
    org, _, _ = env
    org.write_text("* Q\n** TODO Read: foo bar\n   Link: https://bad.test/1\n"
                   "** TODO Read: foo\n   Link: https://ok.test/2\n")
    run(monkeypatch, lambda u: None if "bad" in u else "c", lambda it, c: {"summary": "s"}, limit=2)
    text = org.read_text()
    assert "** TODO Read: foo bar" in text
    assert "** DONE Read: foo\n" in text


def test_output_props_stay_in_the_items_own_section(env):
    """Lean: old_end_search_crosses_heading / findEndSec_in_section."""
    org, _, _ = env
    neighbour = "** TODO Read: other\n   :PROPERTIES:\n   :ID: other-id\n   :END:\n"
    org.write_text("* Q\n** TODO Read: nodrawer\n   Link: https://ok.test/1\n" + neighbour)
    item = R.parse_research_items(limit=5)[0]
    assert item["title"] == "Read: nodrawer"
    assert R.mark_done(item, "notes/lit.md", ["z1"]) is True
    text = org.read_text()
    assert text.endswith(neighbour), "the neighbour's drawer is untouched"
    own = text.split("** TODO Read: other")[0]
    assert ":OUTPUT: [[notes/lit.md]]" in own and ":ZETTELS: [[z1]]" in own
    assert own.index(":PROPERTIES:") < own.index(":OUTPUT:") < own.index(":END:")


def test_output_props_go_into_the_existing_drawer(env):
    org, _, _ = env
    org.write_text("* Q\n** TODO Read: x\n   :PROPERTIES:\n   :ID: x-id\n   :END:\n   Link: https://ok.test/1\n")
    item = R.parse_research_items(limit=5)[0]
    assert item["id"] == "x-id"
    R.mark_done(item, "notes/lit.md", [])
    text = org.read_text()
    assert text.count(":PROPERTIES:") == 1
    assert text.index(":ID: x-id") < text.index(":OUTPUT: [[notes/lit.md]]") < text.index(":END:")


def test_item_is_located_by_id_when_its_heading_was_edited(env):
    org, _, _ = env
    org.write_text("* Q\n** TODO Read: x\n   :PROPERTIES:\n   :ID: x-id\n   :END:\n   Link: https://ok.test/1\n")
    item = R.parse_research_items(limit=5)[0]
    org.write_text(org.read_text().replace("** TODO Read: x", "** TODO Read: x (retitled) :tag:"))
    assert R.mark_done(item, "", []) is True
    assert "** DONE Read: x (retitled) :tag:" in org.read_text()


def test_heading_not_found_is_reported_not_silently_rewritten(env):
    org, logs, _ = env
    org.write_text("* Q\n** TODO Read: other\n")
    assert R.mark_done({"heading_line": "** TODO Read: gone", "title": "Read: gone"}, "x", []) is False
    assert any("not marked DONE" in m for m in logs)
    assert org.read_text() == "* Q\n** TODO Read: other\n"


def test_note_fetch_failure_on_a_substring_heading_does_not_raise(env):
    org, _, _ = env
    org.write_text("* Q\n** TODO Read: foo bar\n")
    assert R.note_fetch_failure({"heading_line": "** TODO Read: foo"}) is None
    assert org.read_text() == "* Q\n** TODO Read: foo bar\n"


def test_attempt_counter_is_not_read_from_the_next_item(env):
    org, _, _ = env
    org.write_text("* Q\n** TODO Read: a\n  *bold* line\n    :FETCH_ATTEMPTS: 2\n")
    # `*bold*` is body text, not a heading: the counter below it is this item's.
    assert R.note_fetch_failure({"heading_line": "** TODO Read: a"}) == 3


def test_summary_names_what_is_parked(env, monkeypatch):
    """Docstring: "The queue drains; the summary names what is parked". The
    journal said "(kept as TODO for retry)" for items that had just been parked,
    named none of them, and was not written at all when nothing succeeded."""
    org, logs, tmp = env
    org.write_text("* Q\n** TODO [#A] Read: paywalled\n   Link: https://bad.test/1\n")
    for _ in range(R.MAX_FETCH_ATTEMPTS):
        run(monkeypatch, lambda u: None, lambda it, c: {"summary": "s"}, limit=1)
    journal = (tmp / "journals" / f"{R.TODAY}.md").read_text()
    assert "Read: paywalled" in journal and "WAITING" in journal
    assert "kept as TODO" not in journal
    assert any("Parked" in m and "Read: paywalled" in m for m in logs)


# ---- research_router: the bounds proved in Lean hold on the real code ----

def test_router_bounds_hold():
    texts = ["", "memory workflow process however", "arxiv paper how we lessons",
             "bloomberg reuters news", "example-repo should fix provenance"]
    open_work = {"provenance": "PR 1", "memory": "PR 2", "workflow": "PR 3"}
    for t in texts:
        prev = None
        for age in [None, 0, 1, 5, 10, 20, 30, 45, 60, 90, 120, 365, 800, 5000]:
            r = RR.classify({"heading": t}, open_work=open_work, age_days=age)
            assert all(0 <= v <= 3 for v in r["relevance"].values())
            assert 0 <= r["score"] <= 18
            if age is not None:
                if prev is not None:
                    assert r["relevance"]["timeliness"] <= prev
                prev = r["relevance"]["timeliness"]
            if r["dest"] == "issue":
                assert r["repo"] and r["relevance"]["roadmap"] > 0
        # roadmap alone never promotes
        no_repo = RR.classify({"heading": "memory workflow"}, open_work=open_work)
        assert no_repo["dest"] != "issue"
