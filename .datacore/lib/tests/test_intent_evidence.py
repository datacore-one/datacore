"""Fresh report checks challenge evidence selection, preservation and rendering."""
from datetime import date
import hashlib
import sys

import pytest

import intent_outline
import intent_review
from intent_merge import completed, decision_records, merge
from intent_sources import IntentInputError
from .test_intent_input_integrity import node, space, write


def test_partial_input_failure_cannot_replace_a_valid_review(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    selected = space(root, 'alpha', 'alpha')
    state = tmp_path / 'state'
    monkeypatch.setenv('DATACORE_STATE', str(state))
    monkeypatch.setattr(sys, 'argv', ['intent_review', '--root', str(root), '--date', '2026-09-13'])
    intent_review.main()
    target = state / 'intent-reviews' / hashlib.sha256(str(root).encode()).hexdigest() / 'Intent-Graph-Review.md'
    before = target.read_bytes()
    (selected / 'org/next_actions.org').write_bytes(b'\xff')
    with pytest.raises(IntentInputError):
        intent_review.main()
    assert target.read_bytes() == before


def test_outline_has_one_private_publication_path_and_includes_root_graph(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'data'
    space(root, 'alpha', 'alpha')
    write(root / '.datacore/intents.org', node('root', 'Root strategy'))
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(sys, 'argv', ['intent_outline', '--root', str(root)])
    assert intent_outline.main() == 0
    assert 'Root strategy' in capsys.readouterr().out
    shared = write(tmp_path / 'shared.md', 'preserve')
    sys.argv += ['--out', str(shared)]
    with pytest.raises(ValueError):
        intent_outline.main()
    assert shared.read_text() == 'preserve'
    sys.argv[-1] = 'Outline.md'
    intent_outline.main()
    outputs = list((tmp_path / 'state').rglob('Outline.md'))
    assert len(outputs) == 1
    assert outputs[0].stat().st_mode & 0o077 == 0


def test_outline_source_is_literal(tmp_path):
    from priority_score import IntentGraph
    write(tmp_path / '.datacore/intents.org', node('root', '<img src="private"> **bold**'))
    result = '\n'.join(intent_outline.outline(IntentGraph.load(tmp_path), ''))
    assert '<img' not in result
    assert '**bold**' not in result


@pytest.mark.parametrize('kind', ['org', 'decision', 'directory-alias'])
def test_completed_and_decision_evidence_cannot_disappear_silently(tmp_path, kind):
    selected = space(tmp_path, 'alpha', 'alpha')
    if kind == 'org':
        (selected / 'org/next_actions.org').write_bytes(b'\xff')
        reader = completed
    elif kind == 'decision':
        path = write(selected / '3-knowledge/decisions/test.md', '')
        path.write_bytes(b'\xff')
        reader = decision_records
    else:
        target = tmp_path / 'outside'
        target.mkdir()
        (selected / 'org/intents.org').unlink()
        (selected / 'org').rmdir()
        (selected / 'org').symlink_to(target, target_is_directory=True)
        reader = completed
    with pytest.raises((ValueError, OSError)):
        reader(tmp_path)


def test_dated_report_uses_completion_dates_and_reports_unknowns(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / 'org/next_actions.org', '''* DONE Shipping old
CLOSED: [2026-01-01 Thu]
* DONE Shipping recent
CLOSED: [2026-09-12 Sat 09:30]
* DONE Shipping undated
#+BEGIN_SRC org
,* DONE Shipping example, not evidence
#+END_SRC
''')
    write(selected / '3-knowledge/decisions/2026-01-01-old.md', 'Decision: no longer pursuing this')
    write(selected / '3-knowledge/decisions/undated.md', 'Decision: no longer pursuing this')
    write(selected / '3-knowledge/decisions/2026-09-12-new.md', 'Decision: no longer pursuing this')
    all_time = merge(tmp_path)
    assert all_time['done'] == 3
    result = merge(tmp_path, date(2026, 9, 1))
    assert result['done'] == 1
    assert result['decisions'] == 1
    assert result['undated_done'] == 1
    assert result['undated_decisions'] == 1
    assert len(result['candidates']) == 1


def test_undated_work_cannot_prove_a_branch_dormant(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / 'org/next_actions.org', '* DONE Shipping\n')
    assert merge(tmp_path, date(2026, 9, 1))['dormant'] == []


def test_archived_copies_cannot_double_count_completed_work(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    for name in ['next_actions.org', 'archive.org']:
        write(selected / 'org' / name, '* DONE Shipping\n:PROPERTIES:\n:ID: same\n:END:\n')
    with pytest.raises(ValueError):
        completed(tmp_path)
