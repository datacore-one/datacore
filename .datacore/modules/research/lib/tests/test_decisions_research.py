"""Owner decision D9 (2026-09-23): :FETCH_ATTEMPTS:, :ANALYSIS_ATTEMPTS: and
:RESULT: are written inside the item's :PROPERTIES: drawer; readers accept the
old under-heading location too; `migrate_research_props.py` moves existing
ones (dry-run by default). Nothing here touches the real queue."""
import importlib.util, pathlib, sys
from unittest.mock import MagicMock

LIB = pathlib.Path(__file__).resolve().parents[1]
for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    if name not in sys.modules:
        sys.modules[name] = MagicMock()
spec = importlib.util.spec_from_file_location("ro_d9", LIB / "research_orchestrator.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
spec = importlib.util.spec_from_file_location("mig_d9", LIB / "migrate_research_props.py")
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

HEAD = "*** TODO [[https://x.test/a][A]]"
DRAWER = ":PROPERTIES:\n:SOURCE: https://x.test/a\n:ID: org-a\n:END:\n"
NEXT = "*** TODO [[https://y.test/b][B]]\n:PROPERTIES:\n:ID: org-b\n:END:\n"


def _drawer(text, head=HEAD):
    lines = text.split("\n")
    hi = lines.index(head)
    s = next(j for j in range(hi, len(lines)) if lines[j].strip() == ":PROPERTIES:")
    e = next(j for j in range(s, len(lines)) if lines[j].strip() == ":END:")
    return lines[hi + 1:s], lines[s + 1:e]


def _setup(tmp_path, monkeypatch, text):
    org = tmp_path / "research_learning.org"
    org.write_text(text)
    monkeypatch.setattr(R, "RESEARCH_ORG", org)
    monkeypatch.setattr(R, "log", lambda m: None)
    return org


ITEM = {"id": "org-a", "heading_line": HEAD, "title": "A"}


def test_d9_counter_is_written_inside_the_drawer(tmp_path, monkeypatch):
    org = _setup(tmp_path, monkeypatch, "* Q\n" + HEAD + "\n" + DRAWER + NEXT)
    assert R.note_fetch_failure(ITEM) == 1
    before, inside = _drawer(org.read_text())
    assert before == [], "nothing between the heading and its drawer"
    assert ":FETCH_ATTEMPTS: 1" in inside


def test_d9_legacy_counter_is_read_and_moved_into_the_drawer(tmp_path, monkeypatch):
    org = _setup(tmp_path, monkeypatch,
                 "* Q\n" + HEAD + "\n    :FETCH_ATTEMPTS: 2\n" + DRAWER + NEXT)
    assert R.note_fetch_failure(ITEM) == 3
    text = org.read_text()
    head = HEAD.replace(" TODO ", " WAITING ")
    before, inside = _drawer(text, head)
    assert before == []
    assert ":FETCH_ATTEMPTS: 3" in inside
    assert any(l.startswith(":RESULT: unfetchable after 3 attempts") for l in inside)
    assert text.count("FETCH_ATTEMPTS") == 1
    assert _drawer(text, NEXT.split("\n")[0])[1] == [":ID: org-b"], "the neighbour is untouched"


def test_d9_item_without_a_drawer_gets_one(tmp_path, monkeypatch):
    org = _setup(tmp_path, monkeypatch, "* Q\n" + HEAD + "\n   Link: https://x.test/a\n" + NEXT)
    assert R.note_analysis_failure({"heading_line": HEAD}) == 1
    lines = org.read_text().split("\n")
    hi = lines.index(HEAD)
    assert lines[hi + 1:hi + 4] == ["    :PROPERTIES:", "    :ANALYSIS_ATTEMPTS: 1", "    :END:"]


def test_d9_drawer_value_wins_over_a_stale_legacy_line(tmp_path, monkeypatch):
    org = _setup(tmp_path, monkeypatch, "* Q\n" + HEAD + "\n    :FETCH_ATTEMPTS: 2\n"
                 ":PROPERTIES:\n:FETCH_ATTEMPTS: 1\n:ID: org-a\n:END:\n")
    assert R.note_fetch_failure(ITEM) == 2
    assert org.read_text().count("FETCH_ATTEMPTS") == 1


def test_d9_reparking_replaces_the_result_instead_of_stacking_it(tmp_path, monkeypatch):
    org = _setup(tmp_path, monkeypatch, "* Q\n" + HEAD + "\n    :RESULT: old reason\n"
                 "    :FETCH_ATTEMPTS: 2\n" + DRAWER)
    R.note_fetch_failure(ITEM)
    text = org.read_text()
    assert text.count(":RESULT:") == 1 and "old reason" not in text


# ---- migration -------------------------------------------------------------

LEGACY = ("* Q\n"
          "*** WAITING [[https://x.test/a][A]]\n"
          "    :RESULT: unfetchable after 3 attempts\n"
          "    :FETCH_ATTEMPTS: 3\n"
          ":PROPERTIES:\n:SOURCE: https://x.test/a\n:ID: org-a\n:END:\n\n"
          "*** DONE [[https://y.test/b][B]]\n"
          "    CLOSED: [2026-09-07 Mon]\n"
          "    :FETCH_ATTEMPTS: 1\n"
          ":PROPERTIES:\n:ID: org-b\n    :OUTPUT: [[x.md]]\n:END:\n"
          "*** TODO [[https://z.test/c][C]]\n"
          "    :ANALYSIS_ATTEMPTS: 2\n"
          "   body text mentioning :RESULT: inline is left alone\n"
          "*** TODO clean\n:PROPERTIES:\n:ID: org-d\n:END:\n")


def test_d9_migration_moves_every_legacy_line_into_its_own_drawer():
    new, report = M.migrate_text(LEGACY)
    assert report["moved"] == 4 and report["items"] == 3 and report["drawers_created"] == 1
    lines = new.split("\n")
    a = lines.index("*** WAITING [[https://x.test/a][A]]")
    assert lines[a + 1] == ":PROPERTIES:"
    assert ":RESULT: unfetchable after 3 attempts" in lines[a + 1:a + 7]
    b = lines.index("*** DONE [[https://y.test/b][B]]")
    assert lines[b + 1] == "    CLOSED: [2026-09-07 Mon]" and lines[b + 2] == ":PROPERTIES:"
    c = lines.index("*** TODO [[https://z.test/c][C]]")
    assert lines[c + 1:c + 4] == ["    :PROPERTIES:", "    :ANALYSIS_ATTEMPTS: 2", "    :END:"]
    assert "   body text mentioning :RESULT: inline is left alone" in lines
    assert M.migrate_text(new) == (new, {"moved": 0, "items": 0, "drawers_created": 0,
                                         "dropped_duplicates": 0})


def test_d9_migration_preserves_what_the_reader_sees():
    new, _ = M.migrate_text(LEGACY)
    for text in (LEGACY, new):
        lines = text.split("\n")
        hi = lines.index("*** WAITING [[https://x.test/a][A]]")
        assert R._get_item_prop(lines, hi, "FETCH_ATTEMPTS")[0] == "3"
        hi = lines.index("*** TODO [[https://z.test/c][C]]")
        assert R._get_item_prop(lines, hi, "ANALYSIS_ATTEMPTS")[0] == "2"


def test_d9_migration_cli_is_dry_run_by_default(tmp_path, capsys):
    f = tmp_path / "q.org"
    f.write_text(LEGACY)
    assert M.main([str(f)]) == 0
    assert f.read_text() == LEGACY
    assert "dry run" in capsys.readouterr().out.lower()
