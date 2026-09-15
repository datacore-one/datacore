"""A cached projection must never become an implicit authoritative stale write."""
from copy import deepcopy

import pytest
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.projector import project
from ledger.projection_state import STATE, base_document, guard_projection, sync_generated, ProjectionConflict


def setup(tmp_path):
    space = tmp_path / '9-drill'
    (space / 'org').mkdir(parents=True)
    (space / '.datacore').mkdir(exist_ok=True)
    (space / '.datacore/ledger-edit-protocol').write_text('1\n')
    log = EventLog(space, 'writer')
    log.append('item.create', {'id': 'one', 'title': 'original title', 'state': 'TODO', 'space': '9-drill',
                              'level': 1, 'tags': [], 'org': {'body': 'original body', 'properties': {'A': 'base'}, 'priority': None}})
    state = fold(read_events(space))
    text = project(state, space=space.name).text
    (space / 'org/next_actions.org').write_text(text)
    (space / STATE).parent.mkdir(parents=True)
    (space / STATE).write_text(base_document(text))
    return space, log, text


def test_unchanged_projection_cannot_revert_new_remote_state(tmp_path):
    space, log, _ = setup(tmp_path)
    log.append('item.update', {'id': 'one', 'title': 'new remote title'})
    before = read_events(space)
    assert sync_generated(space, fold(before), 'writer') == {'updated': 0, 'dismissed': 0}
    assert read_events(space) == before
    assert fold(read_events(space)).items['one'].title == 'new remote title'


def test_disjoint_local_body_and_remote_title_survive(tmp_path):
    space, log, text = setup(tmp_path)
    log.append('item.update', {'id': 'one', 'title': 'new remote title'})
    (space / 'org/next_actions.org').write_text(text.replace('original body', 'new local body'))
    assert sync_generated(space, fold(read_events(space)), 'writer')['updated'] == 1
    state = fold(read_events(space))
    assert state.items['one'].title == 'new remote title'
    assert state.items['one'].payload['org']['body'] == 'new local body'
    assert sync_generated(space, state, 'writer')['updated'] == 0, 'retry is idempotent'


def test_divergent_edit_preserves_both_versions_and_writes_no_event(tmp_path):
    space, log, text = setup(tmp_path)
    log.append('item.update', {'id': 'one', 'title': 'remote title'})
    target = space / 'org/next_actions.org'
    target.write_text(text.replace('original title', 'local title'))
    before = read_events(space)
    with pytest.raises(ProjectionConflict, match='concurrent edit'):
        sync_generated(space, fold(before), 'writer')
    assert 'local title' in target.read_text()
    assert read_events(space) == before


def test_uningested_body_prevents_projection(tmp_path):
    space, _, text = setup(tmp_path)
    with pytest.raises(ProjectionConflict, match='not represented'):
        guard_projection(space, text.replace('original body', 'valuable edit'), text)


def test_missing_base_does_not_make_stale_content_authoritative(tmp_path):
    space, log, text = setup(tmp_path)
    (space / STATE).unlink()
    log.append('item.update', {'id': 'one', 'title': 'newer remote title'})
    before = read_events(space)
    with pytest.raises(ProjectionConflict, match='no projection base'):
        sync_generated(space, fold(before), 'writer')
    assert read_events(space) == before
    assert (space / 'org/next_actions.org').read_text() == text


def test_disjoint_property_edits_merge_without_losing_remote_property(tmp_path):
    space, log, text = setup(tmp_path)
    log.append('item.update', {'id': 'one', 'org': {'properties': {'A': 'base', 'B': 'remote'}}})
    (space / 'org/next_actions.org').write_text(text.replace(':A: base', ':A: local'))
    sync_generated(space, fold(read_events(space)), 'writer')
    assert fold(read_events(space)).items['one'].payload['org']['properties'] == {'A': 'local', 'B': 'remote'}


def test_edited_root_notes_are_not_discarded(tmp_path):
    space, _, text = setup(tmp_path)
    with pytest.raises(ProjectionConflict):
        guard_projection(space, 'valuable root note\n'+text, text)


def test_bootstrap_preamble_cannot_disappear(tmp_path):
    space, _, text = setup(tmp_path)
    (space / STATE).unlink()
    with pytest.raises(ProjectionConflict, match='preamble'):
        guard_projection(space, 'valuable untracked note\n'+text, text)


def test_projection_and_base_roll_back_together_on_write_failure(tmp_path, monkeypatch):
    import ledger_project_org as generator
    import org_transaction as tx
    space, _, text = setup(tmp_path)
    (space / '.datacore/ledger-phase').write_text('1\n')
    target, baseline = space / 'org/next_actions.org', space / STATE
    before = [target.read_bytes(), baseline.read_bytes()]
    original = tx.atomic_write_text
    def fail(path, content):
        if path == baseline:
            raise OSError('snapshot disk failure')
        return original(path, content)
    monkeypatch.setattr(tx, 'atomic_write_text', fail)
    with pytest.raises(OSError, match='snapshot disk failure'):
        generator.project_space(space)
    assert [target.read_bytes(), baseline.read_bytes()] == before


def test_canonical_writer_requires_precondition_and_preserves_on_failure(tmp_path, monkeypatch):
    from ledger.projector import Projection, write, ProjectionConflict as WriteConflict
    import org_transaction as tx
    path = tmp_path / 'output.org'
    path.write_text('authored data\n')
    replacement = Projection('replacement\n', 0)
    with pytest.raises(WriteConflict):
        write(replacement, path)
    assert path.read_text() == 'authored data\n'
    original = tx.atomic_write_text
    def fail(target, content):
        if target == path:
            raise OSError('disk failure before replacement')
        return original(target, content)
    monkeypatch.setattr(tx, 'atomic_write_text', fail)
    with pytest.raises(OSError):
        write(replacement, path, last_written_sha=tx.digest('authored data\n'))
    assert path.read_text() == 'authored data\n'


def test_edited_created_provenance_is_not_silently_erased(tmp_path):
    space, _, text = setup(tmp_path)
    authored = text.replace(':ID: one', ':ID: one\n  :CREATED: [2024-01-03 Wed 12:30]')
    with pytest.raises(ProjectionConflict):
        guard_projection(space, authored, text)
