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


def test_inbox_after_closed_sections_keeps_the_correct_index():
    text = '* DONE old\n* Inbox\n** TODO keep\n* Other\n** TODO other\n'
    out, archive, _ = M.clean(text, '2026-09-10')
    assert out.startswith('* Inbox\n** TODO keep\n')
    assert '* Other\n** TODO other' in out
    assert 'DONE old' in archive


def test_no_open_grandchild_disappears_into_an_archive():
    text = '* Inbox\n** DONE closed\n*** CANCELLED closed child\n**** TODO unfinished\n:PROPERTIES:\n:ID: preserve\n:END:\n'
    out, archive, _ = M.clean(text, '2026-09-10')
    assert '** TODO unfinished' in out and ':ID: preserve' in out
    assert 'unfinished' not in archive and ':ID: preserve' not in archive


def test_failed_archive_write_restores_both_original_files(tmp_path, monkeypatch):
    import sys
    import pytest
    import org_transaction as tx
    org = tmp_path / 'org'
    org.mkdir()
    source = org / 'inbox.org'
    target = org / 'inbox-archive-2026-09-10.org'
    source.write_text(MESS)
    target.write_text('* Archived\n** DONE previous\n')
    originals = {source: source.read_bytes(), target: target.read_bytes()}
    real_write = tx.atomic_write_text
    def fail_target(path, content):
        if path == target:
            raise OSError('injected archive failure')
        real_write(path, content)
    monkeypatch.setattr(tx, 'atomic_write_text', fail_target)
    monkeypatch.setattr(sys, 'argv', ['inbox_cleanup', str(tmp_path), '--apply', '--today', '2026-09-10'])
    with pytest.raises(OSError, match='archive failure'):
        M.main()
    for path, content in originals.items():
        assert path.read_bytes() == content
