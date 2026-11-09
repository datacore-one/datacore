"""CAP-8 (audit B-F5, catalogue GT-06): an oddly formatted or oddly tagged
capture in an inbox never stops its space from saving or syncing.

Seeded failure: ai_task_gate.py gating inbox.org like any task list. The space
pre-commit hook runs it on every staged org/*.org, so one unclarified inbox
capture tagged :AI: without SURFACE/DONE_WHEN refused converge's autosave
("autosave refused by pre-commit hook", nightshift 2-datacore, 2026-09-26
04:25Z on) -- and through B-F4 stopped the whole host.

The gate must still refuse the same task in next_actions.org and other lists:
that is the executor's queue.
"""

import ai_task_gate

CAPTURE = ("* TODO Respond to datacore-one/datacore-mcp#19 -- Unblock npm publish :AI:\n"
           ":PROPERTIES:\n:ID: cap8-0001\n:END:\n")


def _space(tmp_path):
    org = tmp_path / '2-datacore' / 'org'
    org.mkdir(parents=True)
    return org


def test_an_ai_tagged_inbox_capture_does_not_refuse_the_commit(tmp_path, monkeypatch):
    org = _space(tmp_path)
    (org / 'inbox.org').write_text(CAPTURE)
    # The hook passes repo-relative paths from the space root.
    monkeypatch.chdir(org.parent)
    assert ai_task_gate.main(['org/inbox.org']) == 0
    assert ai_task_gate.main([str(org / 'inbox.org')]) == 0


def test_the_same_task_in_next_actions_is_still_refused(tmp_path, monkeypatch):
    org = _space(tmp_path)
    (org / 'inbox.org').write_text(CAPTURE)
    (org / 'next_actions.org').write_text(CAPTURE.replace('cap8-0001', 'cap8-0002'))
    monkeypatch.chdir(org.parent)
    assert ai_task_gate.main(['org/inbox.org', 'org/next_actions.org']) == 1


def test_other_task_lists_are_still_gated(tmp_path, monkeypatch):
    org = _space(tmp_path)
    (org / 'someday.org').write_text(CAPTURE)
    monkeypatch.chdir(org.parent)
    assert ai_task_gate.main(['org/someday.org']) == 1
