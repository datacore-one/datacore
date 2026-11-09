"""KNW-9: My journal is never lost or half-written. A crash or a clash between two writers
leaves the previous page intact.

Kind: deterministic, against a tmp journal page and a private tmp DATACORE_STATE.
Two real journal writers exist today: journal_store.update_journal (the org
transaction: nightshift journal, today_orchestrator, comms) and
file_utils.locked_read_modify_write_text (the research pipeline's "## Research
Processing" section). They take different locks.

Seeded failure: a crash in the middle of a write leaves a truncated page; or two
writers clash and one silently overwrites what the other just wrote (the
research writer read the page, the transaction writer appended, the research
writer then wrote its stale copy back).
"""
import os
import sys
import threading
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import file_utils  # noqa: E402
import journal_store  # noqa: E402

PAGE = "---\ndate: 2026-09-25\ntype: daily\n---\n\n## Morning\n\nWrote the plan.\n"


@pytest.fixture
def page(tmp_path, monkeypatch):
    tmp = tmp_path.resolve()
    monkeypatch.setenv("DATACORE_STATE", str(tmp / "state"))
    p = tmp / "notes" / "journals" / "2026-09-25.md"
    p.parent.mkdir(parents=True)
    p.write_text(PAGE, encoding="utf-8")
    return p


@pytest.mark.parametrize("writer", ["journal_store", "file_utils"])
def test_a_crash_mid_write_leaves_the_previous_page_intact(page, monkeypatch, writer):
    real_replace = os.replace

    def crash(src, dst, *a, **k):
        if Path(dst).resolve() == page:
            raise OSError("simulated crash before the new page is published")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", crash)
    monkeypatch.setattr(os, "rename", crash)
    add = lambda old: (old or "") + "\n## Evening\n\n" + "x" * 50000 + "\n"
    with pytest.raises(Exception):
        if writer == "journal_store":
            journal_store.update_journal(page, add)
        else:
            file_utils.locked_read_modify_write_text(page, add)
    assert page.read_text(encoding="utf-8") == PAGE, f"{writer}: a crash damaged the previous page"


def test_two_writers_clashing_never_lose_an_entry(page):
    read_done, other_done = threading.Event(), threading.Event()
    errors = []

    def research_writer():
        def modifier(old):
            read_done.set()
            other_done.wait(timeout=3)      # the other writer gets its chance, if the lock allows it
            return (old or "") + "\n## Research Processing\n\nProcessed 2 items.\n"
        try:
            file_utils.locked_read_modify_write_text(page, modifier)
        except Exception as e:  # noqa: BLE001
            errors.append(("research", e))

    def session_writer():
        read_done.wait(timeout=3)
        try:
            journal_store.update_journal(page, lambda old: (old or "") + "\n## Session: evals\n\nWrote KNW-9.\n")
        except Exception as e:  # noqa: BLE001
            errors.append(("session", e))
        finally:
            other_done.set()

    a, b = threading.Thread(target=research_writer), threading.Thread(target=session_writer)
    a.start(); b.start(); a.join(20); b.join(20)

    text = page.read_text(encoding="utf-8")
    assert text.startswith(PAGE), "the previous page was not kept"
    lost = [s for s in ("## Research Processing", "## Session: evals") if s not in text]
    refused = [w for w, _ in errors]
    # Refusing one write loudly is acceptable (nothing is lost silently); overwriting is not.
    silently_lost = [s for s in lost if not (
        (s == "## Research Processing" and "research" in refused)
        or (s == "## Session: evals" and "session" in refused))]
    assert not silently_lost, (
        f"two journal writers clashed and {silently_lost} was silently overwritten; errors={errors}")
