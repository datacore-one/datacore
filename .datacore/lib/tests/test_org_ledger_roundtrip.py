"""User-authored fields must survive adapter -> ledger -> rendered Org."""
from argparse import Namespace
import pytest

from ledger.fold import fold
from ledger.genesis import import_space
from ledger.log import EventLog, read_events
from ledger.projector import project
from ledger.projection_state import STATE, base_document, snapshot
import org_workspace_adapter as adapter


def test_section_notes_multiline_properties_and_creation_time_survive(tmp_path):
    space = tmp_path / '9-roundtrip'
    (space / 'org').mkdir(parents=True)
    source = '''* Research
:PROPERTIES:
:ID: section
:CONTEXT: |
:   First line
:     indented second line
:END:
Section notes [[https://example.test/target][must survive]].
** WAITING [#A] An actual task
:PROPERTIES:
:ID: task
:CREATED: [2026-09-11 Fri 08:45]
:END:
Task body with [[id:other-task][linked task]] and *emphasis*.
'''
    (space / 'org/next_actions.org').write_text(source)
    import_space(space)
    text = project(fold(read_events(space))).text
    assert snapshot(text, space.name)['items'] == snapshot(source, space.name)['items']
    assert '08:45' in text and 'https://example.test/target' in text
    assert '[[id:other-task][linked task]]' in text
    assert 'indented second line' in text


def test_adapter_creation_preserves_requested_state_and_update_fields(tmp_path):
    space = tmp_path / '9-roundtrip'
    (space / 'org').mkdir(parents=True)
    (space / '.datacore/events').mkdir(parents=True)
    path = space / 'org/inbox.org'
    path.write_text('#+TITLE: Inbox\n')
    parser = adapter.build_parser()
    added = adapter.cmd_add(parser.parse_args(['add', '--file', str(path), '--heading', 'wait for answer',
        '--state', 'WAITING', '--created', '[2026-09-11 Fri 08:45]']))
    identity = added['id']
    assert fold(read_events(space)).items[identity].payload['state'] == 'WAITING'
    adapter.cmd_update(parser.parse_args(['update', '--file', str(path), '--id', identity,
        '--state', 'NEXT', '--tags', ':research:', '--scheduled', '2026-09-15']))
    item = fold(read_events(space)).items[identity]
    assert item.payload['state'] == 'NEXT'
    assert item.payload['tags'] == ['research']
    assert '2026-09-15' in item.payload['scheduled']
    assert '08:45' in project(fold(read_events(space))).text


def test_phase1_adapter_edit_cannot_restore_stale_remote_properties(tmp_path):
    space = tmp_path / '9-roundtrip'
    (space / 'org').mkdir(parents=True)
    (space / '.datacore').mkdir(exist_ok=True)
    (space / '.datacore/ledger-edit-protocol').write_text('1\n')
    log = EventLog(space, 'writer')
    log.append('item.create', {'id': 'one', 'title': 'base', 'state': 'TODO', 'tags': [], 'level': 1,
                              'org': {'properties': {'A': 'base'}, 'body': '', 'priority': None}})
    text = project(fold(read_events(space))).text
    target = space / 'org/next_actions.org'
    target.write_text(text)
    (space / STATE).parent.mkdir(parents=True)
    (space / STATE).write_text(base_document(text))
    (space / '.datacore/ledger-phase').write_text('1\n')
    log.append('item.update', {'id': 'one', 'title': 'new remote title',
                              'org': {'properties': {'A': 'base', 'B': 'remote'}}})
    parser = adapter.build_parser()
    adapter.cmd_update(parser.parse_args(['update', '--file', str(target), '--id', 'one', '--property', 'A=local']))
    item = fold(read_events(space)).items['one']
    assert item.title == 'new remote title'
    assert item.payload['org']['properties'] == {'A': 'local', 'B': 'remote'}


@pytest.mark.parametrize('body', [
    '# An authored comment must survive.\n',
    '#+begin_example\n:PROPERTIES:\n:ID: example-only\n:END:\n#+end_example\n',
    '#+BEGIN_SRC text\n:PROPERTIES:\n:ID: task\n:END:\n#+END_SRC\n',
    '#+begin_src text\nExample date: [2026-09-11 Mon]\n#+end_src\n',
    '#+begin_example\n:PROPERTIES:\n:ID: example-only\n:END:\n',
    'An example of an incorrect date is [2026-09-11 Mon].\n',
])
def test_literal_body_survives_ingest_and_deployed_projection(tmp_path, body):
    from ledger_project_org import _with_org_header
    space = tmp_path / '9-literal'
    (space / 'org').mkdir(parents=True)
    target = space / 'org/next_actions.org'
    source = '* TODO Task\n:PROPERTIES:\n:ID: task\n:END:\n' + body
    target.write_text(source)
    import_space(space)
    item = fold(read_events(space)).items['task']
    assert body.rstrip() in item.payload['org']['body']
    rendered = _with_org_header(space, target, project(fold(read_events(space))).text)
    assert body.rstrip() in rendered
    assert snapshot(rendered, space.name)['items'] == snapshot(source, space.name)['items']
