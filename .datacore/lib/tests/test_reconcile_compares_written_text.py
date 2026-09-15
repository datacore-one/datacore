"""Reconciliation must compare the org file against what would be WRITTEN.

project() emits the headings and a generated banner, with no in-buffer
settings. ledger_project_org._with_org_header puts the authored `#+` lines
back, and it is that text which lands on disk.

Reconciling against the header-less intermediate meant a file carrying
`#+FILETAGS: :gtd:` disagreed with the ledger on every heading that inherited
the tag -- org applies a file tag to all of them, the intermediate has no file
tag, so effective_tags never matched. 46 of 0-personal's 983 differences were
this alone, and nothing about the comparison could converge.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.log import EventLog  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.projection_state import snapshot  # noqa: E402
from ledger.projector import project  # noqa: E402
import ledger_project_org as projector  # noqa: E402


def _space(tmp_path, preamble):
    space = tmp_path / '0-fixture'
    (space / '.datacore/events').mkdir(parents=True)
    (space / '.datacore').joinpath('space.yaml').write_text(
        'name: fixture\ntype: personal\n', encoding='utf-8')
    (space / 'org').mkdir()
    log = EventLog(space, 'mac')
    log.append('item.create', {'id': 'i1', 'title': 'A task', 'tags': ['work']})
    rendered = project(fold(read_events(space)), space=space.name, as_of=0).text
    (space / 'org/next_actions.org').write_text(preamble + rendered, encoding='utf-8')
    return space


def test_a_file_tag_does_not_make_every_item_disagree(tmp_path):
    space = _space(tmp_path, '#+FILETAGS: :gtd:\n\n')
    target = space / 'org/next_actions.org'
    current = snapshot(target.read_text(encoding='utf-8'), space.name)
    raw = project(fold(read_events(space)), space=space.name, as_of=0).text

    bare = snapshot(raw, space.name)
    differing_before = [i for i, f in current['items'].items() if bare['items'].get(i) != f]
    assert differing_before, 'precondition: the intermediate disagrees because it has no file tag'

    written = snapshot(
        projector._with_org_header(space, target, raw, remember=False), space.name)
    differing_after = [i for i, f in current['items'].items() if written['items'].get(i) != f]
    assert not differing_after, 'comparing the written text, the file tag is on both sides'


def test_a_space_without_a_file_tag_is_unaffected(tmp_path):
    space = _space(tmp_path, '')
    target = space / 'org/next_actions.org'
    current = snapshot(target.read_text(encoding='utf-8'), space.name)
    raw = project(fold(read_events(space)), space=space.name, as_of=0).text
    written = snapshot(
        projector._with_org_header(space, target, raw, remember=False), space.name)
    assert [i for i, f in current['items'].items() if written['items'].get(i) != f] == []
