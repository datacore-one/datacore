"""inbox.org is a collection point: open entries under Inbox, closed ones out (2026-09-05)."""
import importlib.util, pathlib, re

LIB = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("inbox_cleanup", LIB / "inbox_cleanup.py")
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

MESS = """#+TITLE: Inbox
#+FILETAGS: :inbox:

* Inbox
** TODO first capture
:PROPERTIES:
:ID: a1
:END:
** DONE closed inside inbox
CLOSED: [2026-09-01 Tue]
* DONE [#A] processed task
CLOSED: [2026-09-02 Wed]
:PROPERTIES:
:ID: p1
:END:
** TODO [[https://github.com/x/y][captured under a DONE parent]]
:PROPERTIES:
:SOURCE: https://github.com/x/y
:ID: c1
:END:
** DONE closed child of a closed parent
* CANCELLED dropped task
:PROPERTIES:
:ID: p2
:END:
* Half marathon block
** NEXT run 20k
** DONE run 15k
"""


def test_the_invariant_is_restored():
    out, arch, stats = M.clean(MESS, "2026-09-05")
    assert stats == {"inbox_created": False, "moved_into_inbox": 1, "archived": 4}, "four blocks leave: the closed child rides with its parent"
    lines = out.split("\n")
    tops = [l for l in lines if l.startswith("* ")]
    assert tops == ["* Inbox", "* Half marathon block"], tops
    assert not re.search(r"^\*+ (DONE|CANCELLED)\b", out, re.M), "nothing closed stays"
    inbox_end = next(i for i in range(len(lines)) if lines[i].startswith("* Half"))
    inbox = "\n".join(lines[:inbox_end])
    assert "first capture" in inbox and "captured under a DONE parent" in inbox
    assert inbox.index("first capture") < inbox.index("captured under a DONE parent"), "arrivals go to the end"
    assert "** NEXT run 20k" in out
    assert arch.startswith("#+TITLE: Inbox Archive 2026-09-05\n\n* Archived (processed 2026-09-05)\n")
    assert "** DONE [#A] processed task" in arch and "*** DONE closed child of a closed parent" in arch, "top-level entries are demoted, their children with them"
    assert "** CANCELLED dropped task" in arch and "** DONE closed inside inbox" in arch and "** DONE run 15k" in arch
    assert ":ID: p1" in arch and ":ID: p1" not in out


def test_a_file_without_an_inbox_section_gets_one_first():
    out, arch, stats = M.clean("#+TITLE: x\n\n* DONE old\n** TODO orphan\n", "2026-09-05")
    assert stats["inbox_created"] and stats["moved_into_inbox"] == 1 and stats["archived"] == 1
    assert out.split("\n")[2:4] == ["* Inbox", "** TODO orphan"]


def test_a_clean_file_is_left_alone():
    text = "#+TITLE: x\n\n* Inbox\n** TODO a\n** NEXT b\n"
    out, arch, stats = M.clean(text, "2026-09-05")
    assert arch is None and stats["archived"] == 0 and stats["moved_into_inbox"] == 0
    assert out.strip() == text.strip()
