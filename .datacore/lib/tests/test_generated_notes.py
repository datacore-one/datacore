"""Generated knowledge must preserve existing documents and retry safely."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from generated_notes import create_note


def test_colliding_generated_names_preserve_every_body(tmp_path):
    first = create_note(tmp_path, 'same.md', 'original knowledge')
    second = create_note(tmp_path, 'same.md', 'new knowledge')
    assert first != second
    assert first.read_text() == 'original knowledge'
    assert second.read_text() == 'new knowledge'
    assert create_note(tmp_path, 'same.md', 'new knowledge') == second


def test_concurrent_generation_cannot_overwrite_a_note(tmp_path):
    def generate(number):
        return create_note(tmp_path, 'same.md', str(number))
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(generate, range(20)))
    assert len(set(paths)) == 20
    assert {path.read_text() for path in paths} == {str(i) for i in range(20)}


def test_existing_symlink_never_overwrites_external_file(tmp_path):
    target = tmp_path / 'original'
    target.write_text('private original')
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / 'same.md').symlink_to(target)
    with pytest.raises(ValueError, match='symbolic'):
        create_note(notes, 'same.md', 'replacement')
    assert target.read_text() == 'private original'


def test_filename_remains_in_output_directory(tmp_path):
    path = create_note(tmp_path, '../../escape.md', 'content')
    assert path.parent == tmp_path and path.read_text() == 'content'
