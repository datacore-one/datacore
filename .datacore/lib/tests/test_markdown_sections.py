"""Source preservation across real and literal Markdown section boundaries."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from markdown_sections import append_section, sections


@pytest.mark.parametrize('example', [
    '```markdown\n## Nightshift\nexample\n```\n',
    '~~~~\n## Nightshift\n```\n~~~~\n',
    '> ## Nightshift\n> quoted example\n',
    '    ## Nightshift\n    indented example\n',
    '<!--\n## Nightshift\n-->\n',
    '<div>\n## Nightshift\n</div>\n',
    '## Nightshift ideas\nAn unrelated heading.\n',
])
def test_literal_or_partial_heading_is_not_the_target(example):
    source = '# Journal\n\n' + example + '\n## Other\nKeep.\n'
    result = append_section(source, 'Nightshift', 'New summary.')
    assert result.startswith(source)
    assert result.endswith('## Nightshift\n\nNew summary.\n')


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_source_bytes_and_frontmatter_are_preserved(newline):
    source = newline.join(['---', 'note: |', '  ## Nightshift', '---', '',
                           '# Journal', '', '## Nightshift', 'Keep\u2028this.', '', '## Other', 'Keep too.', ''])
    target = [section for section in sections(source) if section.title == 'Nightshift']
    assert len(target) == 1
    prefix, suffix = source[:target[0].end], source[target[0].end:]
    result = append_section(source, 'Nightshift', 'New summary.')
    assert result.startswith(prefix)
    assert result.endswith(suffix)
    assert prefix + suffix == source
    assert 'Keep\u2028this.' in result


def test_duplicate_real_sections_keep_history_and_append_to_last():
    source = '## Nightshift\nFirst.\n\n## Other\nKeep.\n\n## Nightshift\nSecond.\n'
    result = append_section(source, 'Nightshift', 'New summary.')
    assert result.startswith(source)
    assert result.count('New summary.') == 1


@pytest.mark.parametrize('tail', ['```python\nunfinished', '~~~\nunfinished',
                                 '<!-- unfinished', '<script>\nunfinished'])
def test_unfinished_block_refuses_append_instead_of_hiding_output(tail):
    source = '# Journal\n\n' + tail
    with pytest.raises(ValueError, match='unfinished block'):
        append_section(source, 'Nightshift', 'New summary.')


def test_existing_probe_title_cannot_confuse_boundary_check():
    source = '# Journal\n\n## Datacore append boundary\nKeep.\n\n```\nunfinished'
    with pytest.raises(ValueError):
        append_section(source, 'Nightshift', 'New summary.')


def test_setext_section_and_closing_hashes_follow_markdown_semantics():
    for heading in ('Nightshift\n----------\n', '## Nightshift ##\n'):
        source = '# Journal\n\n' + heading + 'Keep.\n\n## Other\nTail.\n'
        result = append_section(source, 'Nightshift', 'New summary.')
        assert result.index('Keep.') < result.index('New summary.') < result.index('## Other')
        assert result.endswith('## Other\nTail.\n')


@pytest.mark.parametrize('title', ['', 'a\nb', 'a\rb', 'a\0b', 'Nightshift ##', ' trailing '])
def test_invalid_section_title_cannot_inject_another_structure(title):
    with pytest.raises(ValueError):
        append_section('# Journal\n', title, 'Summary.')


@pytest.mark.parametrize('content', ['Own summary.\n\n## Other\nInjected.',
                                     '# New title\nInjected.',
                                     'Own summary.\n\n```\nunfinished',
                                     'Own summary.\n\n<!-- unfinished',
                                     '---\nmetadata: injected\n---\nbody'])
def test_appended_content_cannot_escape_its_section_or_hide_the_suffix(content):
    source = '## Nightshift\nKeep.\n\n## Other\nAuthored notes.\n'
    with pytest.raises(ValueError):
        append_section(source, 'Nightshift', content)


def test_completed_literal_example_in_new_content_is_preserved():
    content = '~~~markdown\n## Other\nLiteral example.\n~~~\n'
    source = '## Nightshift\nKeep.\n\n## Other\nAuthored notes.\n'
    result = append_section(source, 'Nightshift', content)
    assert content in result
    assert [s.title for s in sections(result)] == ['Nightshift', 'Other']
