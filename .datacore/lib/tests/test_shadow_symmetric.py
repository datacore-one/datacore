"""A second copy cannot exempt source data from migration preservation checks."""
import pytest
import pathlib, sys
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.log import EventLog  # noqa: E402
from ledger.shadow import compare  # noqa: E402


def test_generated_file_with_an_inbox_twin_is_clean(tmp_path):
    space = tmp_path / "0-personal"; (space / ".datacore" / "events").mkdir(parents=True); (space / "org").mkdir()
    log = EventLog(space, "mac")
    log.append("item.create", {"id": "cap-1", "title": "Captured tab", "state": "TODO", "tags": ["inbox"]})
    log.append("item.create", {"id": "t-2", "title": "Real task", "state": "NEXT"})
    (space / "org" / "inbox.org").write_text("* TODO Captured tab\n:PROPERTIES:\n:ID: cap-1\n:END:\n")
    from ledger.fold import fold
    from ledger.log import read_events
    from ledger.projector import project
    (space / "org" / "next_actions.org").write_text(project(fold(read_events(space)), space="0-personal").text)
    d = compare(space)
    assert d.clean, d
    assert d.org_count == d.projection_count == 2


@pytest.mark.parametrize('change', ['body', 'property', 'section', 'preamble', 'duplicate', 'missing_id'])
def test_shadow_cannot_report_clean_when_full_source_would_be_lost(tmp_path, change):
    from ledger.genesis import import_space
    from ledger.fold import fold
    from ledger.log import read_events
    from ledger.projector import project
    space = tmp_path / '9-drill'
    (space / 'org').mkdir(parents=True)
    target = space / 'org/next_actions.org'
    target.write_text('* Section\n:PROPERTIES:\n:ID: section\n:END:\nSection notes\n'
        '** TODO Task\n:PROPERTIES:\n:ID: task\n:CUSTOM: original\n:END:\nTask body\n')
    import_space(space)
    source = project(fold(read_events(space)), space=space.name).text
    changes = {
        'body': source.replace('Task body', 'Unrecorded valuable notes'),
        'property': source.replace(':CUSTOM: original', ':CUSTOM: unrecorded'),
        'section': source.replace('Section notes', 'Unrecorded section notes'),
        'preamble': 'Unrecorded root note\n' + source,
        'duplicate': source.replace(':ID: section', ':ID: task'),
        'missing_id': source.replace('  :ID: task\n', ''),
    }
    target.write_text(changes[change])
    (space / 'org/inbox.org').write_text('* TODO Twin\n:PROPERTIES:\n:ID: task\n:END:\n')
    diff = compare(space)
    assert not diff.clean
    assert diff.changed or diff.problems
    assert target.read_text() == changes[change]
