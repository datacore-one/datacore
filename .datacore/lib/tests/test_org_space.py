"""Filesystem location cannot silently choose a different ledger authority."""
import pytest

from org_space import ledger_space_for_file, validate_org_write_path
import org_workspace_adapter as adapter


def source(root, name='1-fixture'):
    space = root / name
    (space / 'org').mkdir(parents=True)
    (space / '.datacore/events').mkdir(parents=True)
    path = space / 'org/tasks.org'
    path.write_text('* TODO fixture\n')
    return space, path


def test_own_space_and_whole_installation_alias(tmp_path):
    data = tmp_path / 'data'
    space, path = source(data)
    alias = tmp_path / 'alias'
    alias.symlink_to(data, target_is_directory=True)
    assert ledger_space_for_file(path) == space
    assert ledger_space_for_file(alias / path.relative_to(data)) == space
    assert validate_org_write_path(alias / path.relative_to(data)) == path


@pytest.mark.parametrize('kind', ['missing', 'outside', 'no-own-ledger', 'cross-file', 'cross-org'])
def test_invalid_source_cannot_emit_into_another_space(tmp_path, kind):
    space, path = source(tmp_path)
    other, other_path = source(tmp_path, '2-other')
    (tmp_path / '.datacore/events').mkdir(parents=True)
    if kind == 'missing':
        path.unlink()
    elif kind == 'outside':
        path = space / 'elsewhere.org'
        path.write_text('* TODO fixture\n')
    elif kind == 'no-own-ledger':
        (space / '.datacore/events').rmdir()
    elif kind == 'cross-file':
        path.unlink()
        path.symlink_to(other_path)
    elif kind == 'cross-org':
        path.unlink()
        (space / 'org').rmdir()
        (space / 'org').symlink_to(other / 'org', target_is_directory=True)
    before = sorted(tmp_path.rglob('*.jsonl'))
    assert ledger_space_for_file(path) is None
    assert adapter._ledger_emit(path, 'item.create', {'id': 'fixture', 'title': 'fixture'}) is None
    assert sorted(tmp_path.rglob('*.jsonl')) == before
